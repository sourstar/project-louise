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
    from app.hold import annotations as ann, check as chk, records as rec, snapshot as sn
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
    snap = sn.create(root, NAME, snapshot_id="2026-08", log=quiet)
    held = _embed_as_cut(root, old)
    (root / f"{NAME}.toml").write_text(f'codex = "{old.collection_id}"\nembed = "all"\n', encoding="utf-8")
    chk.attest_landed(root, NAME, old, snap, [s.path for s in snap.shards], key=KEY, platform_image=PLATFORM)
    uids = sorted(_marks(root))
    ed, _ = ann.create(root, "gb-editions", target_ref=NAME, from_snapshot="2026-08",
                       claims=claims_file(a_claim(uids[0], uids[1], subject_lang="en")), platform_image=PLATFORM,
                       snapshot_id="2026-09", key=KEY, now=NOW, nonce="0f" * 16, log=lambda m: None)
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
    return [r for r in pin_run._sweep(root, name, cid) if "/.replaced/" not in f"/{r}"]


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


def test_refused_while_the_worker_runs(held_gb, capsys):
    root, old, _h, _e = held_gb
    p = lock_mod.lock_path(lock_mod.state_dir(root, NAME))
    p.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-I", "-c",
                               "import fcntl, sys, time\n"
                               "f = open(sys.argv[1], 'a+b'); fcntl.flock(f, fcntl.LOCK_EX)\n"
                               "f.seek(0); f.truncate(); f.write(b'{\"what\": \"the node (worker)\", \"pid\": 1}')\n"
                               "f.flush(); print('held', flush=True); time.sleep(60)\n", str(p)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        tree = _tree(root)
        assert _refit(root, *FIT) == 3
        assert "louise node stop" in capsys.readouterr().err
        assert _tree(root) == tree
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


def test_the_dry_run_writes_nothing_and_says_the_rollback_is_a_zfs_snapshot(held_gb, capsys):
    root, _old, _h, _e = held_gb
    tree = _tree(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert "affected    1 document(s): " + _dense(root) in out and "gb-editions" in out
    assert "take a ZFS snapshot of /data and the SHARD dataset first: that is the rollback" in out
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


def test_the_refit_is_a_route_of_the_tree():
    from app import api_commands as ac
    path, p = ac.resolve(["hold", "collection", "refit"])
    assert ac.argv_for(path, p, {"collection": "gutenberg", "layer_param": list(FIT), "dry_run": True}) == \
        ["hold", "collection", "refit", "--layer-param", FIT[0], "--layer-param", FIT[1], "--dry-run", "gutenberg"]
