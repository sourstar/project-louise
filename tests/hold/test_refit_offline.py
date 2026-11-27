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
    m.execute("UPDATE documents SET rdf_done=1, embed_done=1, embed_version=7, fts_done=1, fts_version=2, "
              "entity_version=1, chunks=3, tokens=9")
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
    from app import node_state
    node_state.path().parent.mkdir(parents=True, exist_ok=True)
    ns = sqlite3.connect(node_state.path())
    ns.execute("CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY, value TEXT)")
    ns.execute("INSERT OR REPLACE INTO control(key, value) VALUES(?, ?)", (f"publish:{NAME}", "{}"))
    ns.commit()
    ns.close()
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
    """The bytes the refit may write, stat by stat: both collections' folders,
    their files, platform.toml and node.sqlite."""
    from app import node_state, platform_config
    out = {}
    for p in sorted([*(root / NAME).rglob("*"), *(root / "gb-editions").rglob("*"), *root.glob("*.toml"),
                     platform_config.platform_path(), node_state.path()]):
        if p.is_file() and p.name != "lock" and not p.name.endswith(("-wal", "-shm")):
            out[str(p.relative_to(root)) if p.is_relative_to(root) else str(p)] = \
                (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
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
    from app import node_state
    ns = sqlite3.connect(node_state.path())
    assert ns.execute("SELECT COUNT(*) FROM control WHERE key=?", (f"publish:{NAME}",)).fetchone()[0] == 0
    ns.close()
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
        assert _refit(root, *FIT) == 2, "2, never 3: supervise-pass must not retry it into an unattended start"
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
    assert "affected    1 of 3 document(s) (bound 10): " + _dense(root) in out and "gb-editions" in out
    assert "take a ZFS snapshot of every dataset holding these — that is the rollback" in out
    for what, path in (("gb: vector table", elsewhere), ("gb: SHARD", root / NAME / "shard"),
                       ("gb: build area", root / NAME / "state" / "build"), ("gb-editions: state",
                                                                             root / "gb-editions" / "state")):
        assert f"{what}: {os.path.realpath(path)}" in out, what
    assert "platform.toml:" in out and "node.sqlite:" in out
    for said in ("coverage rows of the old space deleted", "published marker of snapshot 2026-09 set aside",
                 "publisher's row of gb in node.sqlite deleted", "search of gb goes on (quarantine only reports",
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


# ---- the second independent read of 5a93b7c7 ----------------------------------------------------

def test_a_tokenizer_that_cannot_be_loaded_is_refused_before_anything(held_gb, capsys):
    root, _old, _h, _e = held_gb
    tree = _tree(root)
    assert _refit(root, "max_tokens=120", "tokenizer=sha256:" + "ab" * 32) == 2
    err = capsys.readouterr().err
    assert "tokenizer cannot be loaded" in err and "TokenizerMissing" in err
    assert _tree(root) == tree


def test_a_document_the_new_layers_cannot_cut_is_refused_never_counted_a_change(held_gb, monkeypatch, capsys):
    """A cut error counted as a change made every document "affected" and
    the run reset them all (10.5M vectors on gutenberg)."""
    from app import layers
    root, old, _h, _e = held_gb
    real = layers.chunks_for_document

    def cut(coll, row, *a, **k):
        if coll.codex.collection_id != old.collection_id:
            raise RuntimeError("the new layer's tokenizer is broken")
        return real(coll, row, *a, **k)
    monkeypatch.setattr(layers, "chunks_for_document", cut)
    tree = _tree(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 2
    out = capsys.readouterr().out
    assert "affected    0 of 3 document(s)" in out
    assert "REFUSED     (exit 2) the new layers fail to cut 3 document(s)" in out and "tokenizer is broken" in out
    assert _refit(root, *FIT) == 2
    assert "the new layers fail to cut 3 document(s)" in capsys.readouterr().err
    assert _tree(root) == tree


def test_a_change_wider_than_the_bound_is_refused_unless_stated(held_gb, monkeypatch, capsys):
    root, _old, _h, _e = held_gb
    monkeypatch.setattr(refit_mod, "AFFECTED_FLOOR", 0)
    tree = _tree(root)
    assert _refit(root, *FIT) == 2
    assert "state it with --affected-max 1" in capsys.readouterr().err
    assert _tree(root) == tree
    assert _refit(root, *FIT, extra=("--affected-max", "1")) == 0


@pytest.mark.parametrize("which", ["gb", "gb-editions"])
def test_a_half_done_refit_refuses_that_collection_at_boot(held_gb, monkeypatch, which):
    """Invariant 1's per-collection refusal: a worker started on a refit half
    done would cut and embed at the OLD layers what the refit reset."""
    from app import spaces
    from app.hold import records as rec, snapshot as sn
    root, old, _h, ed = held_gb
    if which == "gb":
        restore = _killing(monkeypatch, refit_mod, "_drop_index_entries")
    else:
        restore = _killing(monkeypatch, sn, "sign_snapshot", when=lambda r, n, *a, **k: n == "gb-editions")
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    restore()
    if which == "gb":
        refused = spaces.refuse_boot([rec.local_collection(root, NAME, col.read(root, NAME))])
        assert len(refused) == 1, refused
        why = refused[0][1]
    else:
        from types import SimpleNamespace
        with pytest.raises(spaces.BootValidationError) as e:
            spaces._validate_one(None, SimpleNamespace(name=which,
                                                       manifest_path=root / which / "state" / "manifest.sqlite"))
        why = str(e.value)
    assert "refit" in why and "under way" in why and "louise hold collection refit" in why, why
    assert _refit(root, *FIT) == 0
    assert refit_mod.boot_refusal(root / which / "state") is None
    _, now = col.resolve(root, NAME)
    assert spaces.refuse_boot([rec.local_collection(root, NAME, now)]) == [], "finished, it boots"


def test_a_collection_file_the_writer_cannot_rewrite_refuses_in_the_plan(held_gb, capsys):
    root, old, _h, _e = held_gb
    (root / f"{NAME}.toml").write_text(f'"codex" = "{old.collection_id}"\nembed = "all"\n', encoding="utf-8")
    tree = _tree(root)
    assert _refit(root, *FIT) == 2
    err = capsys.readouterr().err
    assert "collection file cannot take the new id" in err and "Traceback" not in err
    assert _tree(root) == tree


def test_an_indented_codex_line_is_rewritten_and_nothing_else(held_gb):
    root, old, _h, _e = held_gb
    (root / f"{NAME}.toml").write_text(f'  codex = "{old.collection_id}"\nembed = "all"  # kept\n',
                                       encoding="utf-8")
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert (root / f"{NAME}.toml").read_text() == f'  codex = "{new.collection_id}"\nembed = "all"  # kept\n'


def test_parquet_parts_refuse_in_the_plan(held_gb, monkeypatch, capsys):
    from app.hold import codex as cx_mod
    root, _old, _h, _e = held_gb
    monkeypatch.setattr(cx_mod, "container_of", lambda codex: "parquet")
    _refused_untouched(root, capsys, "a refit of a Parquet part is not built")


def test_each_dataset_is_said_with_its_hosts_source_and_mount_point():
    table = refit_mod.mounts("36 35 98:0 /data /data rw,noatime - zfs ssd3/louise rw\n"
                             "37 35 98:1 /lance /data/lance rw - zfs ssd4/louise-lance rw\n"
                             "38 35 98:1 / /config rw - zfs ssd4/config rw\n"
                             "39 35 0:1 / / rw - overlay overlay rw\n"
                             "40 35 98:0 /a\\040b /mnt/a\\040b rw - zfs ssd3/with\\040space rw\n")
    assert refit_mod.where("/data/lance/gb", table) == \
        "/data/lance/gb — on ssd4/louise-lance (zfs), mounted at /data/lance from /lance"
    assert refit_mod.where("/data/collections/gb/state", table).startswith("/data/collections/gb/state — on "
                                                                           "ssd3/louise (zfs), mounted at /data")
    assert refit_mod.where("/config/collections", table) == \
        "/config/collections — on ssd4/config (zfs), mounted at /config"
    assert "on ssd3/with space (zfs), mounted at /mnt/a b" in refit_mod.where("/mnt/a b/x", table)


def test_the_dry_run_says_where_each_dataset_lies(held_gb, capsys):
    root, _old, _h, _e = held_gb
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "gb: SHARD:" in ln)
    assert " — on " in line and "mounted at " in line


def test_the_dry_run_takes_no_index_writer_while_the_worker_runs(held_gb, monkeypatch, capsys):
    root, _old, _h, _e = held_gb
    took = []
    monkeypatch.setattr(refit_mod, "_index_writers", lambda coll, changed: took.append(coll.name) or [])
    holder = _hold(lock_mod.worker_state(root) / lock_mod.WORKER_LOCK)
    try:
        assert _refit(root, *FIT, extra=("--dry-run",)) == 2
        assert "REFUSED     (exit 2)" in capsys.readouterr().out
        assert took == [], "the worker's writers are never taken while it runs"
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.parametrize("switch", ["report", "enforce"])
def test_what_search_does_at_restart_is_said_as_the_quarantine_switch_says(held_gb, capsys, switch):
    from app import copy_state, platform_config
    root, old, _h, ed = held_gb
    platform_config.platform_path().write_text(
        f'[node]\nquarantine = "{switch}"\nquarantine_in_use_before = ["{old.collection_id}", '
        f'"{ed.collection_id}"]\n', encoding="utf-8")
    copy_state._switch.clear()
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    copy_state._switch.clear()
    if switch == "enforce":
        assert "search of gb withheld until the worker's gate passes" in out
    else:
        assert "search of gb goes on (quarantine only reports here" in out and "withheld" not in out


def test_the_worker_s_own_text_index_stage_redoes_the_one_book_at_the_new_layers(held_gb, tmp_path, corpus_dir):
    """The restart, by the worker's real stage: `stage_fts` takes the reset
    book (and it alone), cut by the new layers as a fresh build cuts it. The
    EMBED stage is not run here: it asks the collection file's [encoder] for
    vectors and writes only an answer from a run its gate validated against
    the weights' stored CPU reference — an endpoint, a stated run and a CPU
    reference this fixture's custom-tokenizer collection does not have (the
    harness that has them, tests/test_run_check.py's `node`, builds another
    collection); its selection and its cut are checked instead."""
    from app import db, fts, worker
    from app.hold import records as rec
    root, _old, held, _e = held_gb
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    coll = rec.local_collection(root, NAME, new)
    con = db.connect(coll)
    try:
        row = con.execute(worker._FTS_FRESH, (99,)).fetchone()
        assert row is not None and row["uid"] == _dense(root), "the reset book is the stage's to redo"
        worker.stage_fts(con, dict(row), coll)
        assert con.execute(worker._FTS_FRESH, (99,)).fetchone() is None, "and nothing else"
        done = dict(con.execute("SELECT fts_done, embed_done FROM documents WHERE uid=?", (row["uid"],)).fetchone())
        backlog = con.execute(worker._EMBED_BACKLOG, (99, 10)).fetchone()[0]
    finally:
        con.close()
        fts.close_all()
    assert done == {"fts_done": 1, "embed_done": 0} and backlog == 1, "next, the embed stage: the one book"
    native = int(row["native_id"])
    assert _entries(root, native) > 0
    chunks = worker._load_chunks(dict(row), coll)
    assert len(chunks) != len(held[native]) and all(c["id"].startswith(row["uid"]) for c in chunks)


# ---- the third read of 141b1d2e ---------------------------------------------------------------

def _no_source(root, native=10009, *, vectors=False):
    """A manifest row whose book has no epub, txt or rdf (gutenberg's 34): the
    old layers cannot cut it either."""
    from app import embeddings
    m = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    row = dict(zip([c[1] for c in m.execute("PRAGMA table_info(documents)")],
                   m.execute("SELECT * FROM documents ORDER BY rowid LIMIT 1").fetchone()))
    row.update(uid=f"gutenberg:{native}", native_id=str(native), path=f"{native}/missing.txt")
    cols = [k for k in row]
    m.execute(f"INSERT INTO documents({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", [row[k] for k in cols])
    m.commit()
    m.close()
    if vectors:
        _, codex = col.resolve(root, NAME)
        embeddings.upsert_chunks([{"id": f"gutenberg:{native}:0:1", "book_id": native, "lang": "en", "seq": 0,
                                   "chapter": "", "text": "a chunk of a book no longer held",
                                   "vector": np.ones(codex.space.dim, dtype="float32")}],
                                 collection=_local(root, NAME), dtype=codex.space.dtype)
    return f"gutenberg:{native}"


def test_a_document_uncut_at_both_layers_holding_nothing_is_unchanged_and_said(held_gb, capsys):
    root, old, held, _e = held_gb
    uid = _no_source(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert f"uncut       1 document(s) uncut at both layers, holding nothing: unchanged ({uid}" in out
    assert "affected    1 of 4 document(s)" in out
    before = _marks(root)[uid]
    assert _refit(root, *FIT) == 0
    assert _marks(root)[uid] == before, "never reset: it is not the refit's"


def test_a_document_uncut_at_the_old_layers_that_holds_vectors_refuses(held_gb, capsys):
    root, _old, _h, _e = held_gb
    uid = _no_source(root, vectors=True)
    _refused_untouched(root, capsys, "document(s) the old layers cannot cut hold vector rows or index entries", uid)


def test_a_refit_back_to_an_earlier_codex_sets_aside_in_a_folder_of_its_own(held_gb):
    """120 → 110 → 120 → 110: the fourth met the folder the second left."""
    root, old, _h, _e = held_gb
    ids = []
    for mt in (120, 110, 120, 110):
        assert _refit(root, f"max_tokens={mt}", f"tokenizer={_SHA}", extra=("--affected-max", "3")) == 0, mt
        ids.append(col.read(root, NAME).collection_id)
    assert ids[0] == ids[2] and ids[1] == ids[3] and ids[0] != ids[1]
    folders = sorted(p.name for p in (col.shard_dir_of(root, NAME) / ".replaced").iterdir())
    assert len(folders) == 4 and any(f.endswith(".1") for f in folders), folders
    assert _naming_left(root, NAME, ids[0]) == [] and _naming_left(root, NAME, old.collection_id) == []
    _, ed_now = col.resolve(root, "gb-editions")
    assert [t.collection_id for t in cx.targets_of(ed_now)] == [ids[3]]


def test_the_web_does_not_search_a_collection_whose_refit_is_half_done(held_gb, monkeypatch):
    from fastapi import HTTPException
    from app import collections as collections_mod, query_service
    from app.hold import records as rec
    root, _old, _h, _e = held_gb
    restore = _killing(monkeypatch, refit_mod, "_drop_index_entries")
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    restore()
    coll = rec.local_collection(root, NAME, col.read(root, NAME))
    refused = query_service.refuse_at_start([coll])
    assert [c.name for c, _ in refused] == [NAME]
    assert "not served" in query_service.withheld_reason(coll) and "refit" in query_service.withheld_reason(coll)
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [coll])
    monkeypatch.setattr(collections_mod, "by_name", lambda n: coll if n == NAME else None)
    assert query_service.resolve_collections(None) == [], "left out of the federated set"
    with pytest.raises(HTTPException) as e:
        query_service.resolve_collections([NAME])
    assert e.value.status_code == 403 and "refit" in e.value.detail
    query_service._refused_at_start.clear()
    assert "refit" in query_service.withheld_reason(coll), "and read live, a refit begun since the start"
    assert _refit(root, *FIT) == 0
    done = rec.local_collection(root, NAME, col.read(root, NAME))
    assert query_service.refuse_at_start([done]) == []
    assert not (query_service.withheld_reason(done) or "").startswith("not served")


class _Refusing:
    """An encoder answering `status`/`text` for the second document's input,
    200 for every other — what the real embed stage meets."""

    def __init__(self, status, text):
        self.status, self.text = status, text

    def post(self, url, json=None, timeout=None, headers=None):
        import httpx
        texts = list(json["input"])
        req = httpx.Request("POST", url)
        if any("abstract of 2" in t for t in texts):
            return httpx.Response(self.status, request=req, text=self.text)
        return httpx.Response(200, request=req, json={"data": [
            {"index": i, "embedding": [1.0] + [0.0] * 7} for i in range(len(texts))]})


def _written_by_the_embed_stage(tmp_path, monkeypatch, status, text):
    """The error the worker's real parallel embed stage writes for that
    answer (`_embed_parallel`, `record_error`), never a typed string."""
    from app import collections as collections_mod, config, db, embeddings, worker
    from app.verify import backend_gate
    from tests.test_endpoint_classification import _collection
    monkeypatch.setattr(worker, "_endpoint_paused", {})
    monkeypatch.setattr(config, "BACKEND_GATE", "off")
    backend_gate.reset_cache()
    d = tmp_path / "encoder-stage"
    d.mkdir()
    c = _collection(d, monkeypatch, concurrency=3)
    real = embeddings.httpc.post
    monkeypatch.setattr(embeddings.httpc, "post", _Refusing(status, text).post)
    try:
        con = db.connect(c)
        worker._embed_parallel(con, c, collections_mod.recipe_for(c))
        error = con.execute("SELECT error FROM documents WHERE uid='fake:2'").fetchone()[0]
        con.close()
    finally:
        monkeypatch.setattr(embeddings.httpc, "post", real)
        backend_gate.reset_cache()
    assert error and error.startswith("embed: "), error
    return error


@pytest.mark.parametrize("status, text, cleared", [
    (400, "too many tokens", True),
    (413, "Payload Too Large", True),
    (500, "input (774 tokens) is too large to process. increase the physical batch size", True),
    (500, "request (11187 tokens) exceeds the available context size (8192 tokens)", True),
    (500, "json: error calling MarshalJSON for type schema.Item: json: unsupported value: NaN", False)])
def test_the_reset_clears_the_window_refusal_the_embed_stage_wrote_and_nothing_else(
        held_gb, tmp_path, monkeypatch, capsys, status, text, cleared):
    root, _old, _h, _e = held_gb
    error = _written_by_the_embed_stage(tmp_path, monkeypatch, status, text)
    assert refit_mod.clears(error) is cleared, error
    _reset_with(root, capsys, error, cleared)


@pytest.mark.parametrize("error", ["retracted: the origin retracted the record (06 §6.1)",
                                   "rdf: ParseError: unclosed tag at line 4",
                                   "embed: RuntimeError: embed server HTTP 500: Resource not found"])
def test_the_reset_keeps_every_other_error(held_gb, capsys, error):
    root, _old, _h, _e = held_gb
    assert not refit_mod.clears(error)
    _reset_with(root, capsys, error, False)


def _reset_with(root, capsys, error, cleared):
    dense = _dense(root)
    m = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    m.execute("UPDATE documents SET error=?, retries=3 WHERE uid=?", (error, dense))
    m.commit()
    m.close()
    native = int(_marks(root)[dense][0])
    capsys.readouterr()
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert ("keep their error" in out) is not cleared
    assert _refit(root, *FIT) == 0
    got = _marks(root)[dense]
    assert got[-2:] == ((None, 0) if cleared else (error, 3))
    assert _rows(root, [native])[native] == {} and _entries(root, native) == 0, "deleted all the same"
    assert got[1:8] == (0, 0, 0, 0, 0, 0, 0), "and its marks emptied, to be cut again"


def test_the_annotations_target_marker_is_written_with_the_first_write(held_gb, monkeypatch):
    root, _old, _h, _e = held_gb
    restore = _killing(monkeypatch, refit_mod, "_drop_index_entries")
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    restore()
    assert (root / "gb-editions" / "state" / refit_mod.TARGET_MARKER).is_file()
    assert refit_mod.boot_refusal(root / "gb-editions" / "state"), "refused at boot from the first write"
    assert _refit(root, *FIT) == 0
    assert refit_mod.boot_refusal(root / "gb-editions" / "state") is None


def test_the_codex_line_keeps_its_comment_and_the_file_its_line_endings(held_gb):
    root, old, _h, _e = held_gb
    f = root / f"{NAME}.toml"
    f.write_bytes(f'codex = "{old.collection_id}"   # the encoder window, 2026-11-27\r\nembed = "all"\r\n'.encode())
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert f.read_bytes() == \
        f'codex = "{new.collection_id}"   # the encoder window, 2026-11-27\r\nembed = "all"\r\n'.encode()


# ---- the fourth read of caa9fd8f ------------------------------------------------------------

def test_a_document_the_old_layers_cannot_cut_and_the_new_can_goes_through_holding(held_gb, monkeypatch, capsys):
    """It holds vectors: refused, never counted a change."""
    from app import layers
    root, old, _h, _e = held_gb
    dense = _dense(root)
    real = layers.chunks_for_document

    def cut(coll, row, *a, **k):
        if coll.codex.collection_id == old.collection_id and row["uid"] == dense:
            raise RuntimeError("the old layer cannot cut it")
        return real(coll, row, *a, **k)
    monkeypatch.setattr(layers, "chunks_for_document", cut)
    tree = _tree(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 2
    out = capsys.readouterr().out
    assert "the old layers cannot cut hold vector rows or index entries" in out and dense in out
    assert "holding nothing: unchanged" not in out, "never said unchanged for what it refuses"
    assert _refit(root, *FIT) == 2
    assert _tree(root) == tree


def test_the_dry_run_does_not_call_a_refused_document_unchanged(held_gb, capsys):
    root, _old, _h, _e = held_gb
    uid = _no_source(root, vectors=True)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 2
    out = capsys.readouterr().out
    assert uid in out and "holding nothing: unchanged" not in out


def test_the_old_layers_tokenizer_is_loaded_too(held_gb, tmp_path, monkeypatch, capsys):
    """After a first refit the old layers pin a tokenizer; one that cannot be
    loaded makes every old cut fail: refused in the plan, named."""
    from app import config
    root, _old, _h, _e = held_gb
    assert _refit(root, *FIT) == 0
    tok = Tokenizer(models.WordLevel({"[UNK]": 0, "a": 1}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    raw = tok.to_str().encode("utf-8")
    sha2 = "sha256:" + hashlib.sha256(raw).hexdigest()
    _wd.register(sha2, Tokenizer.from_str(raw.decode("utf-8")))
    try:
        _wd._tokenizers.pop(_SHA, None)
        (config.TOKENIZERS_DIR / f"{_SHA[7:]}.json").unlink()
        tree = _tree(root)
        assert _refit(root, "max_tokens=120", f"tokenizer={sha2}") == 2
        err = capsys.readouterr().err
        assert f"the collection's own layer 0 pins tokenizer {_SHA}, which cannot be loaded" in err
        assert _tree(root) == tree
    finally:
        _wd._tokenizers.pop(sha2, None)


def test_every_reader_of_an_annotations_collection_leaves_it_out_mid_refit(held_gb, monkeypatch):
    """The seeder, the cascade and a question read annotations collections
    through `load_annotations`, which `load_all` does not list: one whose
    target's refit is under way is left out of it, through the boot check's
    own function."""
    import datetime
    from app import collections as collections_mod
    from app.ask import annotations as ask_ann
    from app.hold import cascade, cascade_keeper
    from app.share import seeder
    root, _old, _h, _e = held_gb

    def as_the_node(read):
        """`read` with this root as the node's (its files where the node reads them)."""
        with monkeypatch.context() as m:
            m.setenv("LOUISE_ROOT", str(root.parent))
            m.setattr(collections_mod, "collections_dir", lambda: root)
            return read()
    assert [a.name for a in as_the_node(collections_mod.load_annotations)] == ["gb-editions"]
    restore = _killing(monkeypatch, refit_mod, "_drop_index_entries")
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    restore()
    assert as_the_node(collections_mod.load_annotations) == []
    assert [r.name for r in collections_mod.annotation_refusals()] == ["gb-editions"]
    assert "under way" in collections_mod.annotation_refusals()[0].reason
    assert as_the_node(lambda: seeder.with_annotations([], {})[0]) == [], "the seeder does not share it"
    assert as_the_node(ask_ann._held) == [], "a question does not read it"
    ran = []
    assert cascade_keeper._one(root, "gb-editions", cascade.declarations(root),
                               now=datetime.datetime(2026, 11, 27, tzinfo=datetime.timezone.utc), failed={},
                               log=lambda m: None, run=lambda *a, **k: ran.append(a), stop=None) is None
    assert ran == [], "the cascade does not run it"
    assert _refit(root, *FIT) == 0
    assert [a.name for a in as_the_node(collections_mod.load_annotations)] == ["gb-editions"]


# ---- the fifth read of 51e96bc4 ---------------------------------------------------------------

def _written_by_the_serial_stage(tmp_path, monkeypatch, status, text):
    """The error the worker's SERIAL embed step writes (`_run_step` → its
    error writer) for that answer."""
    from app import config, db, worker
    from app import embeddings
    from app.verify import backend_gate
    from tests.test_endpoint_classification import _collection
    monkeypatch.setattr(worker, "_endpoint_paused", {})
    monkeypatch.setattr(config, "BACKEND_GATE", "off")
    backend_gate.reset_cache()
    d = tmp_path / "serial-stage"
    d.mkdir()
    c = _collection(d, monkeypatch, concurrency=1)
    real = embeddings.httpc.post
    monkeypatch.setattr(embeddings.httpc, "post", _Refusing(status, text).post)
    try:
        con = db.connect(c)
        row = con.execute("SELECT * FROM documents WHERE uid='fake:2'").fetchone()
        worker._run_step(con, c, "embed", row)
        error = con.execute("SELECT error FROM documents WHERE uid='fake:2'").fetchone()[0]
        con.close()
    finally:
        monkeypatch.setattr(embeddings.httpc, "post", real)
        backend_gate.reset_cache()
    assert error and error.startswith("embed: "), error
    return error


@pytest.mark.parametrize("status, text", [
    (422, "input is longer than the model's window"),
    (500, "input (774 tokens) is too large to process. increase the physical batch size"),
    (500, "VeGa: this input cannot be cut at the window exactly")])
def test_the_serial_stage_s_window_refusal_is_cleared(held_gb, tmp_path, monkeypatch, capsys, status, text):
    root, _old, _h, _e = held_gb
    error = _written_by_the_serial_stage(tmp_path, monkeypatch, status, text)
    assert refit_mod.clears(error), error
    _reset_with(root, capsys, error, True)


def test_a_vega_input_refusal_through_the_parallel_stage_is_cleared(held_gb, tmp_path, monkeypatch, capsys):
    root, _old, _h, _e = held_gb
    error = _written_by_the_embed_stage(tmp_path, monkeypatch, 500, "VeGa: this input cannot be cut exactly")
    assert refit_mod.clears(error), error
    _reset_with(root, capsys, error, True)


def test_a_500_without_a_window_refusal_s_words_is_kept():
    assert not refit_mod.clears("embed: InputRefused: embed server HTTP 500: the model crashed")
    assert refit_mod.clears("embed: InputRefused: embed server HTTP 500: request (11187 tokens) exceeds the "
                            "available context size (8192 tokens)")
    assert refit_mod.clears("embed: InputRefused: embed server HTTP 422: anything at all")


def test_documents_gaining_their_first_cut_are_a_change_even_when_they_are_the_only_one(
        held_gb, monkeypatch, capsys):
    """"Nothing to refit" is decided after the documents the old layers could
    not cut and the new ones can are counted."""
    from app import embeddings, fts, layers
    root, old, _h, _e = held_gb
    m = _marks(root)
    other = next(u for u in m if u.endswith("10003"))
    native = int(m[other][0])
    coll = _local(root, NAME)
    embeddings.delete_books([native], coll)
    for g in fts.existing_groups(coll):
        if fts.books_held(g, [native], coll):
            fts.delete_books(g, [native], coll)
    fts.close_all()
    real = layers.chunks_for_document

    def cut(c, row, *a, **k):
        if c.codex.collection_id == old.collection_id and row["uid"] == other:
            raise RuntimeError("no text the old layers can cut")
        return real(c, row, *a, **k)
    monkeypatch.setattr(layers, "chunks_for_document", cut)
    wide = ("max_tokens=100000", f"tokenizer={_SHA}")         # cuts every fitting document as before
    assert _refit(root, *wide, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert f"affected    1 of 3 document(s) (bound 10): {other}" in out and "nothing to refit" not in out
    assert _refit(root, *wide) == 0
    assert _marks(root)[other][1:8] == (0, 0, 0, 0, 0, 0, 0)


def test_the_uncut_line_names_the_error_of_the_document_it_shows(held_gb, capsys):
    root, _old, _h, _e = held_gb
    held_one = _no_source(root, 10008, vectors=True)       # first in rowid order: refused
    shown = _no_source(root, 10009)                          # unchanged: the one the line shows
    assert _refit(root, *FIT, extra=("--dry-run",)) == 2
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("  uncut"))
    assert shown in line and f"the first: {shown}:" in line and held_one not in line
    refused = next(ln for ln in out.splitlines() if "old layers cannot cut hold" in ln)
    assert held_one in refused


def test_an_annotations_collection_left_out_mid_refit_is_said_wherever_one_is_read(held_gb, monkeypatch, capsys):
    from fastapi import HTTPException
    from app import collections as collections_mod, effective_config, query_service
    from app.share import offer
    root, _old, _h, ed = held_gb

    def as_the_node(read):
        with monkeypatch.context() as m:
            m.setenv("LOUISE_ROOT", str(root.parent))
            m.setattr(collections_mod, "collections_dir", lambda: root)
            m.setattr(collections_mod, "config_toml_path", lambda n: root / f"{n}.toml")
            return read()
    restore = _killing(monkeypatch, refit_mod, "_drop_index_entries")
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    restore()
    # `hold config show`
    shown = as_the_node(effective_config.show)
    assert any(r["name"] == "gb-editions" and "under way" in r["reason"] for r in shown["refused"]), shown
    # a query naming it: refused with the reason, not "unknown"
    with pytest.raises(HTTPException) as e:
        as_the_node(lambda: query_service.resolve_annotations(["gb-editions"]))
    assert e.value.status_code == 403 and "under way" in e.value.detail and "unknown" not in e.value.detail
    # `annotations derive`
    capsys.readouterr()
    assert as_the_node(lambda: louise.main(["hold", "annotations", "derive", "gb-editions", "--root",
                                            str(root)])) == 2
    assert "is not served now" in capsys.readouterr().err
    # held all the same: never offered as missing
    assert ed.collection_id in as_the_node(offer.held_ids)
    # the Library's refused list
    from fastapi.testclient import TestClient
    from app import config, spaces, web
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("abc123", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")

    from app.api import app as the_app

    def page():
        with TestClient(the_app) as c:
            c.headers["X-Admin-Token"] = "sekrit"
            return c.get("/admin/library").text
    text = as_the_node(page)
    assert "gb-editions" in text and "under way" in text
