"""A1l IN PLACE (operator, 2026-11-27, for gutenberg: "In place"; on the con
that the case is new to the spec: "rule 1").

`hold collection refit <name> --layer-param max_tokens=N --layer-param
tokenizer=sha256:…` on a `fixed` layer: the collection keeps its name, its
directories and every file at its path; its Codex changes where it lies; only
the document a window over the limit cuts otherwise is reset; the parts
holding it are written again; a seeded sample of the rest is proven; a
failed proof rolls everything back. Nothing is copied and nothing is linked.

End to end on a real collection; only the card is stood in for (the CPU
reference and the gate, tested where they live)."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app import louise
from app.hold import carry as carry_mod, codex as cx, collection as col, lock as lock_mod, refit as refit_mod
from app.hold.codex import CodexError
from app.plugins.wikidata import chunker as _wd
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_carry import _local, quiet
from tests.hold.test_collection_new_sister import card  # noqa: F401  (the fixture)
from tests.hold.test_template import recipe

#: 400-character windows (tokens × 4), paragraph-aligned; the fixture's prose
#: holds ~75 words a window, under the 120 a window may hold
LAYERS = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {"tokens": 100, "overlap": 0}},
           "input": {"form": "text"}}]
#: one book's paragraph of 300 one-letter words: its windows are 200 tokens
DENSE = " ".join(["a"] * 300) + "."
NAME = "gb"


def _tok() -> Tokenizer:
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    return tok


#: the tokenizer's file, and the sha256 a layer pins it by (a spawned child
#: of the pool loads it from TOKENIZERS_DIR as the node's do)
_TOK_JSON = _tok().to_str().encode("utf-8")
_SHA = "sha256:" + hashlib.sha256(_TOK_JSON).hexdigest()
FIT = ("max_tokens=120", f"tokenizer={_SHA}")


@pytest.fixture
def words(tmp_path, monkeypatch):
    """One token per whitespace-separated word, pinned under `_SHA`, in the
    registry and in TOKENIZERS_DIR."""
    from app import config
    d = tmp_path / "tokenizers"
    d.mkdir()
    (d / f"{_SHA[7:]}.json").write_bytes(_TOK_JSON)
    monkeypatch.setenv("TOKENIZERS_DIR", str(d))
    monkeypatch.setattr(config, "TOKENIZERS_DIR", d)
    tok = Tokenizer.from_str(_TOK_JSON.decode("utf-8"))
    _wd.register(_SHA, tok)
    yield tok
    _wd._tokenizers.pop(_SHA, None)


def _embed_as_cut(root, name, codex) -> dict[int, dict[str, str]]:
    """Every document's chunks AS ITS LAYERS CUT THEM in its vector table, its
    space pinned and every mark set, a chunk hash a document and a gate
    verdict: what a collection embedded by the book holds."""
    from app import db, embeddings, layers, plugin_io
    coll = _local(root, name)
    con = db.connect(coll)
    try:
        con.execute("INSERT INTO space(id, space_id, dim, pinned_at) VALUES(1, ?, ?, 0)",
                    (codex.space_id, codex.space.dim))
        docs = [dict(r) for r in con.execute("SELECT rowid, * FROM documents ORDER BY rowid")]
    finally:
        con.close()
    plugin = coll.plugin_instance()
    rng = np.random.default_rng(11)
    rows, held = [], {}
    for d in docs:
        title, sections = plugin_io.title_and_sections(plugin, d, coll)
        for c in layers.chunks_for_document(coll, d, sections, title=title):
            rows.append({"id": c["id"], "book_id": int(d["native_id"]), "lang": d["lang"] or "en",
                         "seq": int(c["seq"]), "chapter": c.get("chapter") or "", "text": c["text"],
                         "vector": rng.standard_normal(codex.space.dim).astype("float32")})
            held.setdefault(int(d["native_id"]), {})[c["id"]] = c["text"]
    embeddings.upsert_chunks(rows, collection=coll, dtype=codex.space.dtype)
    # the text index as the index stage writes it: a real one, one group a language
    from app import fts
    by_book: dict[int, list[dict]] = {}
    for r in rows:
        by_book.setdefault(r["book_id"], []).append({"id": r["id"], "chapter": r["chapter"], "text": r["text"]})
    langs = {int(d["native_id"]): d["lang"] or "en" for d in docs}
    fts.index_books([(langs[b], b, chunks) for b, chunks in by_book.items()], coll)
    fts.close_all()
    m = sqlite3.connect(root / name / "state" / "manifest.sqlite")
    m.execute("UPDATE documents SET embed_done=1, embed_version=7, fts_done=1, fts_version=2, "
              "entity_version=1, chunks=3, tokens=9")
    m.execute("INSERT INTO chunk_hashes(text_hash, chunk_id, native_id) "
              "SELECT randomblob(8), uid || ':0:1', native_id FROM documents")
    m.execute("INSERT INTO control(key, value) VALUES('embed_gate', '{\"run\": \"old\"}')")
    m.commit()
    m.close()
    (root / name / "index").mkdir(parents=True, exist_ok=True)
    (root / name / "index" / "words").write_text("the text index")
    return held


@pytest.fixture
def held_gb(isolated_config, tmp_path, corpus_dir, words):
    """`gb` cutting with `fixed{tokens 100, overlap 0}`, one of its books
    holding a paragraph whose windows the encoder's window would cut, built
    into parts of two records, frozen, embedded as its layer cuts, with its
    collection file beside it."""
    from app.hold import records as rec, snapshot as sn
    book = corpus_dir / "10002" / "pg10002.txt.utf8"
    text = book.read_text(encoding="utf-8")
    at = text.index("*** END OF THE PROJECT GUTENBERG EBOOK")
    book.write_text(text[:at] + DENSE + "\n\n" + text[at:], encoding="utf-8")
    root = tmp_path / "collections"
    old = col.create_from_recipe(root, "gutenberg", recipe(layers=LAYERS), platform_image=PLATFORM,
                                 embed=fake_embed, embed_query=fake_query_embed, name=NAME, key=KEY, now=NOW)
    rec.build(root, NAME, corpus_dir=corpus_dir, records_per_shard=2, log=quiet)
    rec.write_validation(root, NAME)
    sn.create(root, NAME, snapshot_id="2026-08", log=quiet)
    held = _embed_as_cut(root, NAME, old)
    (root / f"{NAME}.toml").write_text(f'# the operator\'s note\ncodex = "{old.collection_id}"\nembed = "all"\n'
                                       f'slices = ["en"]\n', encoding="utf-8")
    return root, old, held


def _refit(root, *params, extra=()):
    args = ["hold", "collection", "refit", NAME, "--root", str(root), *extra]
    for p in params:
        args += ["--layer-param", p]
    return louise.main(args)


def _marks(root) -> dict[str, tuple]:
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    try:
        return {r[0]: tuple(r[1:]) for r in con.execute(
            "SELECT uid, native_id, embed_done, embed_version, fts_done, fts_version, entity_version, chunks, "
            "tokens FROM documents ORDER BY rowid")}
    finally:
        con.close()


def _held(root, books) -> dict[int, dict[str, str]]:
    from app import embeddings
    got = embeddings.held_chunks(_local(root, NAME), books)
    return {b: {cid: v["text"] for cid, v in got.of(b).items()} for b in books}


def _reindexed(marks: dict[str, tuple], uid: str) -> dict[str, tuple]:
    """`marks` with `uid`'s index marks emptied (fts_done, fts_version,
    entity_version), what a rollback leaves for the worker to index again."""
    native, done, version, _f, _fv, _e, chunks, tokens = marks[uid]
    return {**marks, uid: (native, done, version, 0, 0, 0, chunks, tokens)}


def _dense_uid(root) -> str:
    return next(uid for uid in _marks(root) if uid.endswith("10002"))


def _control(root, key):
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    try:
        row = con.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _pin(root):
    return carry_mod._pin(root / NAME / "state" / "manifest.sqlite")[0]


def _tree(root) -> dict[str, tuple[int, int]]:
    """Every file under the collection and its file, by path: (size, mtime) —
    but the write lock's file and SQLite's -wal/-shm, which taking the lock
    and opening the manifest write."""
    out = {}
    for p in sorted([*(root / NAME).rglob("*"), root / f"{NAME}.toml"]):
        if p.name == "lock" or p.name.endswith(("-wal", "-shm")):
            continue
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def _snapshot(root):
    from app.hold import seed
    return seed.latest_snapshot(root, NAME)


# ---- the refit, end to end ----------------------------------------------------------

def test_the_refit_keeps_the_name_and_every_file_and_redoes_only_the_document_the_window_cuts(held_gb, card):
    root, old, held = held_gb
    before, dense = _marks(root), _dense_uid(root)
    snap0 = _snapshot(root)
    index_before = (root / NAME / "index" / "words").read_text()
    assert _refit(root, *FIT) == 0
    name, new = col.resolve(root, NAME)
    assert name == NAME and new.collection_id != old.collection_id and new.space_id != old.space_id
    assert [l.chunker.params for l in new.layers] == [{"tokens": 100, "overlap": 0, "max_tokens": 120,
                                                       "tokenizer": _SHA}]
    assert new.space.encoder == old.space.encoder and new.space.calibration == old.space.calibration
    assert (new.derived_from, new.continues, new.replaces) == (old.derived_from, old.continues, old.replaces)
    # the change set, exactly: its marks emptied, every other document's kept
    after = _marks(root)
    assert set(after) == set(before)
    assert {uid for uid in after if after[uid] != before[uid]} == {dense}
    native = int(after[dense][0])
    assert after[dense][1:] == (0, 0, 0, 0, 0, 0, 0)
    books = sorted(held)
    mine = _held(root, books)
    assert mine[native] == {}, "its rows gone: the worker embeds it at the new layer"
    assert all(mine[b] == held[b] for b in books if b != native), "every other document's vectors kept"
    assert (root / NAME / "index" / "words").read_text() == index_before, "the text index where it lies"
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    try:
        assert con.execute("SELECT COUNT(*) FROM chunk_hashes WHERE native_id=?", (native,)).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM chunk_hashes").fetchone()[0] == len(books) - 1
    finally:
        con.close()
    assert _pin(root) == new.space_id, "re-pinned"
    assert _control(root, "embed_gate") is None, "the old space's gate verdict is not the new one's"
    # the collection file: its codex line alone
    text = (root / f"{NAME}.toml").read_text()
    assert text == (f'# the operator\'s note\ncodex = "{new.collection_id}"\nembed = "all"\nslices = ["en"]\n')
    # the parts: the dense book's part written again, every other one the same bytes
    snap1 = _snapshot(root)
    assert snap1.collection_id == new.collection_id and snap1.snapshot_id == snap0.snapshot_id
    m = refit_mod.read_marker(root, NAME)
    rebuilt = {(p["slice"], p["n"]) for p in m["steps"]["changes"]["parts"]}
    assert len(rebuilt) == 1 and m["steps"]["changes"]["parts"][0]["uids"] == [dense]
    a = {(s.slice, int(s.n)): s for s in snap0.shards}
    b = {(s.slice, int(s.n)): s for s in snap1.shards}
    assert set(a) == set(b)
    for k in a:
        if k in rebuilt:
            assert b[k].chunks > a[k].chunks and b[k].chunks_root != a[k].chunks_root and b[k].root != a[k].root
            assert (b[k].records_root, b[k].records, b[k].first_id, b[k].last_id) == \
                   (a[k].records_root, a[k].records, a[k].first_id, a[k].last_id)
        else:
            assert b[k].to_dict() == a[k].to_dict(), "an unchanged part's entry, writer included"
    # set aside, never deleted: the old Codex, its snapshot and the old version of the part
    sa = refit_mod.shard_aside(root, NAME, old.collection_id)
    assert cx.loads((sa / "codex.json").read_bytes()).collection_id == old.collection_id
    assert (sa / "snapshots" / "2026-08.json").is_file()
    (path,) = [p["path"] for p in m["steps"]["changes"]["parts"]]
    from app.hold import shard as shard_mod
    assert shard_mod.pieces_root_of(sa / "parts" / path) == a[next(iter(rebuilt))].root
    assert m["done"] and m["predecessor"]["collection_id"] == old.collection_id
    assert m["steps"]["proof"]["compared"] == len(books) - 1 and not m["steps"]["proof"]["failures"]
    assert m["steps"]["proof"]["parts_compared"] >= 1
    assert card["order"] == [("cpu-reference", NAME), ("gate", NAME)]
    from app import node_state
    assert NAME not in node_state.paused_collections(), "resumed after its gate"


def test_the_dry_run_writes_nothing_and_says_the_change_set(held_gb, capsys):
    root, old, _held_ = held_gb
    tree = _tree(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert f"change set  1 of " in out and _dense_uid(root) in out
    assert "1 cut otherwise by the new layers" in out and "set aside" in out
    assert _tree(root) == tree, "not a byte written"
    assert col.read(root, NAME).collection_id == old.collection_id
    assert refit_mod.read_marker(root, NAME) is None


def test_the_rollback_puts_back_the_codex_the_pin_the_rows_the_marks_and_the_file(held_gb, card):
    root, old, held = held_gb
    before, text, snap0 = _marks(root), (root / f"{NAME}.toml").read_text(), _snapshot(root)
    assert _refit(root, *FIT) == 0
    dense = _dense_uid(root)
    assert _refit(root, extra=("--rollback",)) == 0
    assert col.read(root, NAME).collection_id == old.collection_id
    assert _pin(root) == old.space_id
    assert _marks(root) == _reindexed(before, dense), \
        "every mark back, the change set's index marks emptied: the worker indexes it again (fix 5: reset)"
    assert _held(root, sorted(held)) == held, "the vector table at its recorded version"
    assert _control(root, "embed_gate") == '{"run": "old"}'
    assert (root / f"{NAME}.toml").read_text() == text
    assert _snapshot(root).hash == snap0.hash
    for s in snap0.shards:
        from app.hold import shard as shard_mod
        assert shard_mod.pieces_root_of(root / NAME / "shard" / s.path) == s.root
    assert refit_mod.read_marker(root, NAME) is None, "out of the next refit's way"
    # and a refit runs again from the start
    assert _refit(root, *FIT) == 0
    assert col.read(root, NAME).collection_id != old.collection_id


def test_a_failed_proof_rolls_back_by_itself(held_gb, card, monkeypatch):
    root, old, held = held_gb
    before = _marks(root)
    real = carry_mod._resign_proof

    def wrong(*a, **k):
        got = real(*a, **k)
        return {**got, "mismatched": ["gutenberg:ghost"]}
    monkeypatch.setattr(carry_mod, "_resign_proof", wrong)
    assert _refit(root, *FIT) != 0
    assert col.read(root, NAME).collection_id == old.collection_id
    assert _marks(root) == _reindexed(before, _dense_uid(root))
    assert _held(root, sorted(held)) == held and _pin(root) == old.space_id
    assert card["order"] == [], "no gate for a refit that did not hold"
    from app import node_state
    assert NAME not in node_state.paused_collections()


class _Boom(Exception):
    pass


@pytest.mark.parametrize("crash_at", list(range(1, 14)))
def test_a_stop_after_any_step_resumes_to_the_same_collection(held_gb, monkeypatch, crash_at):
    """A crash right after the marker records each step (and so between any
    two writes the marker orders): the same run again ends where an
    uninterrupted one ends."""
    root, old, held = held_gb
    new_layers = louise._layers_with(old, louise._layer_params(list(FIT)))
    real = refit_mod._save
    seen = {"n": 0}

    def crashing(r, n, m):
        real(r, n, m)
        seen["n"] += 1
        if seen["n"] == crash_at:
            raise _Boom(f"after save {crash_at}")
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "test")
    try:
        monkeypatch.setattr(refit_mod, "_save", crashing)
        try:
            refit_mod.run(root, NAME, new_layers, log=quiet)
            finished_first = True
        except _Boom:
            finished_first = False
        monkeypatch.setattr(refit_mod, "_save", real)
        layers_again = louise._layers_with(refit_mod.predecessor(root, NAME), louise._layer_params(list(FIT)))
        m = refit_mod.run(root, NAME, layers_again, log=quiet)
    finally:
        lk.release()
    assert m["done"] and (finished_first or seen["n"] == crash_at)
    _, new = col.resolve(root, NAME)
    dense = _dense_uid(root)
    native = int(_marks(root)[dense][0])
    assert [uid for uid, v in _marks(root).items() if v[1:] == (0, 0, 0, 0, 0, 0, 0)] == [dense]
    mine = _held(root, sorted(held))
    assert mine[native] == {} and all(mine[b] == held[b] for b in held if b != native)
    assert _pin(root) == new.space_id and _snapshot(root).collection_id == new.collection_id
    assert not m["steps"]["proof"]["failures"]


def test_the_change_set_pass_runs_in_the_parse_pool(held_gb, monkeypatch):
    """Multi-threading is a must: a window a document, two spawned processes,
    each loading the pinned tokenizer from TOKENIZERS_DIR as the node's
    children do; the same change set as in one process."""
    from app import config, parallel
    root, old, _held_ = held_gb
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    monkeypatch.setenv("PARSE_PROCESSES", "2")
    spawned = {"n": 0}
    real_each = parallel.each_part

    def counting(fn, tasks, *, limit=None):
        if fn.__name__ == "cut_window" and len(tasks) > 1 and (limit or 2) > 1:
            spawned["n"] += 1
        return real_each(fn, tasks, limit=limit)
    monkeypatch.setattr(parallel, "each_part", counting)
    layers = louise._layers_with(old, louise._layer_params(list(FIT)))
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "test")
    try:
        m = refit_mod.run(root, NAME, layers, window=1, processes=2, log=quiet)
    finally:
        lk.release()
    assert spawned["n"] == 1, "the pass went to the pool"
    assert m["done"] and m["steps"]["changes"]["changed"] == 1
    assert m["steps"]["changes"]["shown"] == [_dense_uid(root)]


# ---- refusals ---------------------------------------------------------------------

def test_anything_but_the_encoder_window_is_refused_in_words(held_gb, capsys):
    root, old, _held_ = held_gb
    tree = _tree(root)
    assert _refit(root, "tokens=50") != 0
    assert "more than the encoder window" in capsys.readouterr().err
    assert _refit(root, "max_tokens=120") != 0, "the window is stated whole, with its tokenizer"
    assert _refit(root, "words=3") != 0
    assert "no layer's chunker states it" in capsys.readouterr().err
    assert _refit(root) != 0, "no param at all"
    assert _tree(root) == tree and col.read(root, NAME).collection_id == old.collection_id


def test_a_refit_done_is_not_done_twice(held_gb, card, capsys):
    root, _old, _held_ = held_gb
    assert _refit(root, *FIT) == 0
    capsys.readouterr()
    assert _refit(root, *FIT) != 0
    assert "has it already" in capsys.readouterr().err


def test_new_codex_refuses_another_layer_change_and_keeps_the_lineage(held_gb):
    import dataclasses
    root, old, _held_ = held_gb
    params = dict(old.layers[0].chunker.params, max_tokens=120, tokenizer=_SHA)
    window = dataclasses.replace(old.layers[0], chunker=dataclasses.replace(old.layers[0].chunker, params=params))
    new = refit_mod.new_codex(old, (window,))
    assert new.version >= old.version and new.credits == old.credits and new.descriptive == old.descriptive
    other = dataclasses.replace(old.layers[0], chunker=dataclasses.replace(
        old.layers[0].chunker, params=dict(params, tokens=50)))
    with pytest.raises(CodexError, match="more than the encoder window"):
        refit_mod.new_codex(old, (other,))
    with pytest.raises(CodexError, match="nothing to refit"):
        refit_mod.new_codex(old, old.layers)


def test_the_refit_holds_no_hard_link_anywhere():
    """The operator, 2026-11-27: "Never use hard links, always consciously
    load from a path". The files this refit is built from name none."""
    here = Path(louise.__file__).resolve().parent
    for f in (here / "hold" / "refit.py", here / "collection_file.py", here / "louise.py",
              here / "hold" / "snapshot.py", here / "layers.py"):
        text = f.read_text(encoding="utf-8")
        for bad in ("os.link(", "hardlink_to(", "link_to(", "cp -al", "cp --link"):
            assert bad not in text, f"{f.name}: {bad}"


def test_the_refit_is_a_route_of_the_tree():
    """POST /api/hold/collection/refit (07 §7.4): the command as typed, its
    repeatable window flag a list in the body."""
    from app import api_commands as ac
    path, p = ac.resolve(["hold", "collection", "refit"])
    argv = ac.argv_for(path, p, {"collection": "gutenberg", "layer_param": list(FIT), "dry_run": True})
    assert argv == ["hold", "collection", "refit", "--layer-param", FIT[0], "--layer-param", FIT[1],
                    "--dry-run", "gutenberg"]


def _ask_marker(root):
    from app.ask import facets as ask_facets
    return ask_facets.read_marker(root / NAME / "index")


def test_the_ask_indexes_marker_follows_the_same_records(held_gb, card):
    """Facets, edges and content hashes are functions of the records alone:
    the maintainer's marker is re-keyed to the new snapshot, not counted
    again (RULE 1), and put back by a rollback."""
    from app.ask import facets as ask_facets
    root, old, _held_ = held_gb
    snap0 = _snapshot(root)
    ask_facets.write_marker(root / NAME / "index", snap0.snapshot_id, snap0.hash,
                            facets=ask_facets.facets_digest(old))
    assert _refit(root, *FIT) == 0
    snap1 = _snapshot(root)
    assert snap1.hash != snap0.hash and _ask_marker(root) == (snap1.snapshot_id, snap1.hash)
    assert _refit(root, extra=("--rollback",)) == 0
    assert _ask_marker(root) == (snap0.snapshot_id, snap0.hash)


def test_a_finished_refit_is_not_rolled_back_once_the_table_was_written(held_gb, card, capsys):
    from app import embeddings
    root, old, held = held_gb
    assert _refit(root, *FIT) == 0
    native = int(_marks(root)[_dense_uid(root)][0])
    _, new = col.resolve(root, NAME)
    embeddings.upsert_chunks([{"id": f"gutenberg:{native}:0:1", "book_id": native, "lang": "en", "seq": 0,
                               "chapter": "", "text": "the worker's", "vector": np.ones(new.space.dim, "float32")}],
                             collection=_local(root, NAME), dtype=new.space.dtype)
    capsys.readouterr()
    assert _refit(root, extra=("--rollback",)) != 0
    assert "written since the refit ended" in capsys.readouterr().err
    assert col.read(root, NAME).collection_id == new.collection_id, "nothing changed"


def test_a_later_refit_takes_the_finished_one_as_its_predecessor(held_gb, card):
    root, old, held = held_gb
    assert _refit(root, *FIT) == 0
    _, first = col.resolve(root, NAME)
    assert _refit(root, "max_tokens=100", f"tokenizer={_SHA}") == 0
    _, second = col.resolve(root, NAME)
    assert len({old.collection_id, first.collection_id, second.collection_id}) == 3
    m = refit_mod.read_marker(root, NAME)
    assert m["predecessor"]["collection_id"] == first.collection_id and m["done"]
    assert (refit_mod.state_aside(root, NAME, old.collection_id) / "refit.json").is_file(), \
        "the first refit's record kept with what it set aside"
    assert cx.loads((refit_mod.shard_aside(root, NAME, first.collection_id) / "codex.json").read_bytes()) \
        .collection_id == first.collection_id


# ---- the independent read of e492ebaf, fixed one by one -------------------------

def _index_count(root, native: int) -> int:
    from app import fts
    coll = _local(root, NAME)
    return sum(fts.books_held(g, [native], coll) for g in fts.existing_groups(coll))


def _set(root, sql: str, *params) -> None:
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    con.execute(sql, params)
    con.commit()
    con.close()


def _row(root, uid: str, *cols):
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    try:
        return con.execute(f"SELECT {', '.join(cols)} FROM documents WHERE uid=?", (uid,)).fetchone()
    finally:
        con.close()


def test_1_an_errored_document_is_compared_reset_and_its_part_written_again(held_gb, card):
    """The dense book holds an error (InputRefused, as an over-window chunk
    would give an encoder): it is compared all the same, reset with its error
    cleared, and its part written again."""
    root, old, _held_ = held_gb
    dense = _dense_uid(root)
    _set(root, "UPDATE documents SET error='InputRefused: 8621 tokens', retries=3, embed_done=0 WHERE uid=?",
         dense)
    snap0 = _snapshot(root)
    assert _refit(root, *FIT) == 0
    m = refit_mod.read_marker(root, NAME)
    assert m["steps"]["changes"]["shown"] == [dense] and m["steps"]["changes"]["errored"] == 1
    assert _row(root, dense, "error", "retries") == (None, 0), "met by the stages afresh"
    (part,) = m["steps"]["changes"]["parts"]
    assert part["uids"] == [dense], "the part chosen by cutting the part's own records"
    a = {(s.slice, int(s.n)): s for s in snap0.shards}[(part["slice"], part["n"])]
    b = {(s.slice, int(s.n)): s for s in _snapshot(root).shards}[(part["slice"], part["n"])]
    assert b.chunks > a.chunks and b.chunks_root != a.chunks_root


def test_1_an_embedded_changed_document_loses_its_rows_and_its_index_entries_the_rest_keep_theirs(held_gb, card):
    """The node's case (München, 2026-11-27: PG 69207 embedded, 62 rows): its
    vector rows AND its text-index entries go; the worker cuts, indexes and
    embeds it again."""
    root, old, held = held_gb
    dense = _dense_uid(root)
    native = int(_marks(root)[dense][0])
    others = [b for b in held if b != native]
    assert _index_count(root, native) > 0 and all(_index_count(root, b) > 0 for b in others)
    assert _refit(root, *FIT) == 0
    assert _index_count(root, native) == 0, "its entries gone from the text index"
    assert all(_index_count(root, b) > 0 for b in others), "every other document's kept"
    assert _held(root, [native])[native] == {}
    m = refit_mod.read_marker(root, NAME)
    assert m["steps"]["switch"]["index_entries"] >= 1 and m["steps"]["proof"]["index_entries"] == 0


def test_1_a_window_that_cuts_nothing_otherwise_is_refused_and_leaves_nothing(held_gb, capsys):
    from app import node_state
    root, old, _held_ = held_gb
    tree = _tree(root)
    assert _refit(root, "max_tokens=100000", f"tokenizer={_SHA}") != 0
    assert "nothing to refit" in capsys.readouterr().err
    assert _tree(root) == tree and refit_mod.read_marker(root, NAME) is None
    assert NAME not in node_state.paused_collections()


def test_1_a_proof_that_compared_nothing_rolls_back(held_gb, card, capsys):
    root, old, held = held_gb
    _set(root, "UPDATE documents SET embed_done=0")           # nothing the proof could compare
    before = _marks(root)
    assert _refit(root, *FIT) != 0
    assert "compared no document" in capsys.readouterr().err
    assert col.read(root, NAME).collection_id == old.collection_id
    assert _marks(root) == _reindexed(before, _dense_uid(root)) and _held(root, sorted(held)) == held


def test_2_a_shared_text_index_is_refused_naming_its_members(held_gb, monkeypatch, capsys):
    from app import text_index
    root, old, _held_ = held_gb
    monkeypatch.setattr(text_index, "placement", lambda r, n: {"dir": ".text-index/abc", "identity": "abc"})
    real = col._held
    monkeypatch.setattr(col, "_held", lambda r: [*real(r), ("gb-sister", old)])
    tree = _tree(root)
    assert _refit(root, *FIT) != 0
    err = capsys.readouterr().err
    assert "shared with gb-sister" in err and "not built" in err
    assert _tree(root) == tree


def test_2_the_text_index_writer_held_by_the_worker_asks_for_a_stop_before_any_switch_write(held_gb, card,
                                                                                         monkeypatch, capsys):
    from app import fts
    root, old, _held_ = held_gb
    real = fts._writer

    def busy(key, idx):
        raise ValueError("Failed to acquire Lockfile: LockBusy")
    monkeypatch.setattr(fts, "_writer", busy)
    assert _refit(root, *FIT) == 3
    assert "louise node stop" in capsys.readouterr().err
    assert col.read(root, NAME).collection_id == old.collection_id
    m = refit_mod.read_marker(root, NAME)
    assert m["steps"]["changes"]["done"] and not m["steps"].get("switch"), "nothing of the switch written"
    monkeypatch.setattr(fts, "_writer", real)
    assert _refit(root, *FIT) == 0, "after the stop, the same command resumes"


def _crash_rollback(monkeypatch, root, k: int):
    """A rollback stopped after its k-th marker save, or its k-th rename."""
    real_save, real_rename = refit_mod._save, refit_mod._rename
    seen = {"save": 0, "rename": 0}

    def save(r, n, m):
        real_save(r, n, m)
        if m.get("rollback") is not None:
            seen["save"] += 1
            if seen["save"] == k:
                raise _Boom(f"rollback save {k}")

    def rename(src, dst):
        got = real_rename(src, dst)
        if "rejected" in str(dst) or ".rolled-back-" in str(dst) or str(dst).endswith(("codex.json", "snapshots",
                                                                                            "build")):
            seen["rename"] += 1
            if seen["rename"] == k - 100:
                raise _Boom(f"rollback rename {k - 100}")
        return got
    monkeypatch.setattr(refit_mod, "_save", save)
    monkeypatch.setattr(refit_mod, "_rename", rename)
    return real_save, real_rename


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
def test_3_a_rollback_stopped_at_any_sub_step_resumes(held_gb, card, monkeypatch, k):
    root, old, held = held_gb
    before, text, snap0 = _marks(root), (root / f"{NAME}.toml").read_text(), _snapshot(root)
    dense = _dense_uid(root)
    assert _refit(root, *FIT) == 0
    real_save, real_rename = _crash_rollback(monkeypatch, root, k)
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "test")
    try:
        try:
            refit_mod.rollback(root, NAME, log=quiet)
        except _Boom:
            pass
        monkeypatch.setattr(refit_mod, "_save", real_save)
        monkeypatch.setattr(refit_mod, "_rename", real_rename)
        if refit_mod.read_marker(root, NAME) is not None:
            refit_mod.rollback(root, NAME, log=quiet)
    finally:
        lk.release()
    assert col.read(root, NAME).collection_id == old.collection_id and _pin(root) == old.space_id
    assert _marks(root) == _reindexed(before, dense) and _held(root, sorted(held)) == held
    assert (root / f"{NAME}.toml").read_text() == text and _snapshot(root).hash == snap0.hash
    from app.hold import records as rec
    claim = json.loads((root / NAME / rec.STATE / rec.BUILD / rec.STAGING_CLAIM).read_text())
    assert claim["collection_id"] == old.collection_id, "the old staging back, not filed as rejected"
    assert refit_mod.read_marker(root, NAME) is None


def test_3_a_rollback_whose_recorded_version_was_pruned_is_refused_before_anything_moves(held_gb, card, capsys):
    from datetime import timedelta
    root, old, _held_ = held_gb
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    for _d, t in refit_mod._tables(_local(root, NAME)):
        t.cleanup_old_versions(older_than=timedelta(0), delete_unverified=True)
    tree = _tree(root)
    assert _refit(root, extra=("--rollback",)) != 0
    err = capsys.readouterr().err
    assert "no longer holds the version" in err and "a refit back" not in err
    assert col.read(root, NAME).collection_id == new.collection_id
    assert {k: v for k, v in _tree(root).items() if not k.endswith("refit.json")} == \
        {k: v for k, v in tree.items() if not k.endswith("refit.json")}, "nothing moved"


def test_4_a_failed_cpu_reference_leaves_it_paused_and_the_same_command_goes_straight_to_the_gate(
        held_gb, card, monkeypatch):
    from app import node_state
    root, old, _held_ = held_gb
    calls = {"n": 0}

    def cpu_ref(a):
        calls["n"] += 1
        card["order"].append(("cpu-reference", a.collection))
        return 1 if calls["n"] == 1 else 0
    monkeypatch.setattr(louise, "cmd_embedder_cpu_reference", cpu_ref)
    assert _refit(root, *FIT) == 1
    assert NAME in node_state.paused_collections(), "paused until its gate"
    assert not refit_mod.gated(refit_mod.read_marker(root, NAME))
    assert _refit(root, *FIT) == 0, "the params are the collection's own now: straight to the gate"
    assert card["order"] == [("cpu-reference", NAME), ("cpu-reference", NAME), ("gate", NAME)]
    assert refit_mod.gated(refit_mod.read_marker(root, NAME))
    assert NAME not in node_state.paused_collections()


def test_5_a_finished_refit_the_worker_indexed_rolls_back_with_its_index_marks_reset(held_gb, card):
    root, old, held = held_gb
    before = _marks(root)
    dense = _dense_uid(root)
    assert _refit(root, *FIT) == 0
    _set(root, "UPDATE documents SET fts_done=1, fts_version=2, entity_version=1, chunks=8 WHERE uid=?", dense)
    assert _refit(root, extra=("--rollback",)) == 0
    assert _marks(root)[dense][3:6] == (0, 0, 0), "indexed at the new layer: indexed again at the old one"
    assert {u: v for u, v in _marks(root).items() if u != dense} == {u: v for u, v in before.items() if u != dense}


def test_6_annotations_naming_the_pre_refit_id_apply_to_the_refit_collection(held_gb, card):
    """gutenberg-book-editions names gutenberg's pre-refit id: every reader of
    an annotations target accepts it, as a carry's twin; a rollback takes it
    back."""
    from app.ask import annotations as facts, works
    from app.hold import annotation_methods, annotations as ann
    from tests.hold.test_annotations import a_claim, claims_file
    root, old, _held_ = held_gb
    uids = sorted(_marks(root))
    codex, _snap = ann.create(root, "gb-editions", target_ref=NAME, from_snapshot="2026-08",
                              claims=claims_file(a_claim(uids[0], uids[1], subject_lang="en")),
                              platform_image=PLATFORM, snapshot_id="2026-09", key=KEY, now=NOW,
                              nonce="0f" * 16, log=lambda m: None)
    assert [t.collection_id for t in cx.targets_of(codex)] == [old.collection_id]
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert facts.answers_for(root, NAME, new.collection_id) == (new.collection_id, old.collection_id)
    assert works._ids_of(_local(root, NAME)) == (new.collection_id, old.collection_id)
    assert ann._target_holder(root, old.collection_id)[0] == NAME, "the update and the cascade's holder"
    assert col._target_names(root, codex) == [NAME], "`hold collection show` and the Library's card"
    assert col.show(root, "gb-editions")["targets"][0]["target"] == NAME
    assert annotation_methods._targets_of(root, "gb-editions")[0][0] == NAME
    from app.hold import cascade
    decl = cascade.declarations(root) if hasattr(cascade, "declarations") else None
    if decl is not None and hasattr(decl, "holders"):
        assert NAME in decl.holders.get(old.collection_id, ())
    assert _refit(root, extra=("--rollback",)) == 0
    assert facts.answers_for(root, NAME, old.collection_id) == (old.collection_id,), "rolled back: its own id"


def test_7_a_change_set_gone_stale_before_the_switch_is_computed_again(held_gb, monkeypatch):
    """A stop after `changes`, then a row embedded at the old layer
    meanwhile: the switch does not run on a stale set; it is computed again."""
    from app import embeddings
    root, old, held = held_gb
    real = refit_mod._save

    def stop_after_changes(r, n, m):
        real(r, n, m)
        if (m["steps"].get("changes") or {}).get("done") and not m["steps"].get("switch"):
            raise _Boom("after changes")
    monkeypatch.setattr(refit_mod, "_save", stop_after_changes)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_save", real)
    # the worker embedding at the old layer meanwhile: a row written again (the
    # same chunk, as a re-embed writes it), the table's version moved
    some = sorted(held)[0]
    cid, text = sorted(held[some].items())[0]
    embeddings.upsert_chunks([{"id": cid, "book_id": some, "lang": "en", "seq": int(cid.split(":")[2]),
                               "chapter": "", "text": text, "vector": np.ones(old.space.dim, "float32")}],
                             collection=_local(root, NAME), dtype=old.space.dtype)
    logs: list[str] = []
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "resume")
    try:
        m = refit_mod.run(root, NAME, louise._layers_with(old, louise._layer_params(list(FIT))), log=logs.append)
    finally:
        lk.release()
    assert any("stale" in line and "vector table moved" in line for line in logs)
    assert m["done"] and m["steps"]["changes"]["lance"] != {}


def test_8_the_change_set_file_is_written_whole_and_a_torn_line_is_dropped(tmp_path):
    p = tmp_path / "changed.jsonl"
    refit_mod._write_changed(p, [[1, "gutenberg:1", 1], [5, "gutenberg:5", 5]])
    refit_mod._append_changed(p, [[9, "gutenberg:9", 9]])
    assert refit_mod.read_changed(p) == [[1, "gutenberg:1", 1], [5, "gutenberg:5", 5], [9, "gutenberg:9", 9]]
    with p.open("a", encoding="utf-8") as f:
        f.write('[12,"gutenberg:1')                             # a stop inside an append
    assert refit_mod.read_changed(p)[-1] == [9, "gutenberg:9", 9], "the torn last line dropped"
    p.write_text('[1,"gutenberg:1",1]\n[oops\n[3,"gutenberg:3",3]\n', encoding="utf-8")
    with pytest.raises(CodexError, match="line 2"):
        refit_mod.read_changed(p)
    assert not (tmp_path / "changed.jsonl.tmp").exists()


# ---- crashes BETWEEN a write and its marker save ---------------------------------

def _resume(root, old):
    """The same refit run again (lock held, as the command holds it)."""
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "resume")
    try:
        return refit_mod.run(root, NAME, louise._layers_with(refit_mod.predecessor(root, NAME),
                                                             louise._layer_params(list(FIT))), log=quiet)
    finally:
        lk.release()


def _first(root, old):
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "first")
    try:
        refit_mod.run(root, NAME, louise._layers_with(old, louise._layer_params(list(FIT))), log=quiet)
    except _Boom:
        return
    finally:
        lk.release()
    raise AssertionError("the injected crash did not happen")


def _ends_as_by_the_book(root, old, held):
    _, new = col.resolve(root, NAME)
    dense = _dense_uid(root)
    native = int(_marks(root)[dense][0])
    assert new.collection_id != old.collection_id and _pin(root) == new.space_id
    assert [u for u, v in _marks(root).items() if v[1:] == (0, 0, 0, 0, 0, 0, 0)] == [dense]
    mine = _held(root, sorted(held))
    assert mine[native] == {} and all(mine[b] == held[b] for b in held if b != native)
    assert _index_count(root, native) == 0
    snap = _snapshot(root)
    assert snap.collection_id == new.collection_id
    m = refit_mod.read_marker(root, NAME)
    assert m["done"] and not m["steps"]["proof"]["failures"]
    (part,) = m["steps"]["changes"]["parts"]
    got = {(s.slice, int(s.n)): s for s in snap.shards}[(part["slice"], part["n"])]
    assert (got.chunks, got.chunks_root) == (part["chunks"], part["chunks_root"])


def test_crash_after_the_codex_json_rename(held_gb, monkeypatch):
    root, old, held = held_gb
    real = refit_mod._rename

    def rename(src, dst):
        got = real(src, dst)
        if dst.name == "codex.json" and refit_mod.ASIDE in str(dst):
            raise _Boom("after the codex.json rename")
        return got
    monkeypatch.setattr(refit_mod, "_rename", rename)
    _first(root, old)
    assert not (col.shard_dir_of(root, NAME) / "codex.json").is_file(), "the crash left no codex.json in place"
    monkeypatch.setattr(refit_mod, "_rename", real)
    _resume(root, old)
    _ends_as_by_the_book(root, old, held)


def test_crash_between_the_reset_commit_and_delete_books(held_gb, monkeypatch):
    from app import embeddings
    root, old, held = held_gb
    real = embeddings.delete_books
    monkeypatch.setattr(embeddings, "delete_books", lambda *a, **k: (_ for _ in ()).throw(_Boom("delete")))
    _first(root, old)
    monkeypatch.setattr(embeddings, "delete_books", real)
    _resume(root, old)
    _ends_as_by_the_book(root, old, held)


def test_crash_after_the_part_replace_before_the_build_db_update(held_gb, monkeypatch):
    root, old, held = held_gb
    real = refit_mod._place

    def place(tmp, path):
        real(tmp, path)
        raise _Boom("after the part replace")
    monkeypatch.setattr(refit_mod, "_place", place)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_place", real)
    _resume(root, old)
    _ends_as_by_the_book(root, old, held)


@pytest.mark.parametrize("step", ["parts", "proof"])
def test_crash_after_parts_done_and_after_proof_done(held_gb, card, monkeypatch, step):
    root, old, held = held_gb
    real = refit_mod._save

    def save(r, n, m):
        real(r, n, m)
        if (m["steps"].get(step) or {}).get("done"):
            raise _Boom(f"after {step} done")
    monkeypatch.setattr(refit_mod, "_save", save)
    with pytest.raises(_Boom):
        _refit(root, *FIT)
    from app import node_state
    assert NAME in node_state.paused_collections(), "stays paused until its gate"
    monkeypatch.setattr(refit_mod, "_save", real)
    assert _refit(root, *FIT) == 0, "the same command finishes it: the proof, or straight to the gate"
    _ends_as_by_the_book(root, old, held)
    assert card["order"] == [("cpu-reference", NAME), ("gate", NAME)]
    assert NAME not in node_state.paused_collections()


# ---- the second read of 10cc6f34 -----------------------------------------------

@pytest.fixture
def signer(monkeypatch):
    """This node's key and image, as the node has them."""
    from app.bundles import keys as node_keys
    from app.hold import equivalence
    monkeypatch.setattr(node_keys, "load_signing_key", lambda *a, **k: KEY)
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    assert equivalence.running_image() == PLATFORM
    return KEY


def test_r2_1_quarantine_enforced_the_new_id_is_in_use_by_its_own_attested_checks(held_gb, card, signer,
                                                                                  monkeypatch):
    """Attestations are keyed on the id: without the `attest` step an
    enforced quarantine withholds gutenberg from every search. The node lists
    the OLD id in `quarantine_in_use_before`; the new one is cleared by its
    own roots and chunks, run on the part written again, while paused."""
    from app import copy_state, platform_config
    from app.hold import envelope as env
    root, old, _held_ = held_gb
    platform_config.platform_path().parent.mkdir(parents=True, exist_ok=True)
    platform_config.platform_path().write_text(
        f'[node]\nquarantine = "enforce"\nquarantine_in_use_before = ["{old.collection_id}"]\n', encoding="utf-8")
    copy_state._switch.clear()
    copy_state.reset_cache()
    me = env.key_text(KEY.verify_key)
    monkeypatch.setattr(copy_state, "_gate", lambda *a, **k: (copy_state.Test("gate", copy_state.PASSED,
                                                                              "the gate ran"), True, ""))
    assert copy_state.enforced()

    def state(codex):
        copy_state.reset_cache()
        return copy_state.evaluate(root, NAME, codex, licence="not-required", key=me,
                                   before=copy_state.in_use_before(codex))
    assert state(old).in_use, "in use before, by the operator's decision"
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert not copy_state.in_use_before(new), "the new id is in no list"
    got = state(new)
    assert got.in_use, got.reasons
    assert {t.name: t.status for t in got.tests}["roots-and-chunks"] == copy_state.PASSED
    m = refit_mod.read_marker(root, NAME)
    assert set(m["steps"]["attest"]["attestations"]) >= {"roots", "chunks"}
    assert m["steps"]["attest"]["parts"] == [p["path"].removeprefix("records/").rsplit(".", 2)[0]
                                            for p in m["steps"]["changes"]["parts"]]
    # without them: quarantine
    att = root / NAME / "state" / "attestations" / new.collection_id.split(":", 1)[1]
    att.rename(att.with_name("away"))
    assert not state(new).in_use, "an enforced quarantine withholds a copy with no attested checks"


def _published_fixture(root, old):
    """What the node shared of gutenberg for testing: the seeder's serving,
    the publisher's pass, a published marker, its attestations, a peer's
    chain, a carry's record naming it."""
    from app import node_state
    from app.hold import cascade
    _set(root, "SELECT 1")                                     # the manifest is there
    m = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    m.execute("INSERT OR REPLACE INTO control(key, value) VALUES('share_served', ?)",
              (json.dumps({"collection_id": old.collection_id}),))
    m.commit()
    m.close()
    node_state.set_paused("someone-else", False, by="test")       # node.sqlite exists
    n = sqlite3.connect(node_state.path())
    n.execute("CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT)")
    n.execute("INSERT OR REPLACE INTO control(key, value) VALUES(?, ?)", (f"publish:{NAME}", "2026-11-01 pass"))
    n.commit()
    n.close()
    hexid = old.collection_id.split(":", 1)[1]
    files = {cascade.published_path(root, NAME, "2026-08"): {"collection_id": old.collection_id},
             root / NAME / "state" / "attestations" / hexid / "a1.json": {"collection_id": old.collection_id},
             root / NAME / "state" / "chain" / f"{hexid}.json": {"collection_id": old.collection_id},
             root / NAME / "state" / "carry.json": {"done": True, "from": "sha256:" + "5e" * 32,
                                                    "to": old.collection_id, "rebuilt": True}}
    for p, body in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body), encoding="utf-8")
    return {str(p.relative_to(root / NAME)): p.read_bytes() for p in files}


def test_r2_2_what_was_shared_under_the_old_id_is_said_set_aside_and_put_back(held_gb, card, capsys):
    """The operator, 2026-11-27: shared for testing only — superseded, never
    refused; every item said; set aside, never deleted; a rollback puts each
    back."""
    from app import node_state
    root, old, _held_ = held_gb
    before = _published_fixture(root, old)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert "shared      under the old id: the seeder served it" in out
    assert "the publisher made a pass for it" in out and "fetched from a peer" in out
    assert "names it    state/attestations/" in out and "state/carry.json: repoint" in out
    assert "host step   a ZFS snapshot" in out and "attest      roots, records and chunks" in out
    assert _refit(root, *FIT) == 0
    out = capsys.readouterr().out
    assert "superseded (shared for testing only, RULE 1)" in out and "see a new collection" in out
    _, new = col.resolve(root, NAME)
    ta = refit_mod.asides(root, NAME, refit_mod.read_marker(root, NAME))[1]
    for rel in before:
        if rel == "state/carry.json":
            assert json.loads((root / NAME / rel).read_text())["to"] == new.collection_id, "re-pointed"
            assert (ta / refit_mod.FILES / rel).read_bytes() == before[rel], "its old bytes kept"
        else:
            assert (ta / refit_mod.FILES / rel).read_bytes() == before[rel], f"{rel} set aside, not deleted"
    n = sqlite3.connect(node_state.path())
    assert n.execute("SELECT COUNT(*) FROM control WHERE key=?", (f"publish:{NAME}",)).fetchone()[0] == 0
    n.close()
    assert _refit(root, extra=("--rollback",)) == 0
    for rel, data in before.items():
        assert (root / NAME / rel).read_bytes() == data, f"{rel} put back"
    n = sqlite3.connect(node_state.path())
    assert n.execute("SELECT value FROM control WHERE key=?", (f"publish:{NAME}",)).fetchone()[0] == \
        "2026-11-01 pass"
    n.close()
    assert _control(root, "share_served") is not None


def test_r2_2_an_official_publication_refuses_in_the_one_condition(held_gb, monkeypatch, capsys):
    root, old, _held_ = held_gb
    monkeypatch.setattr(refit_mod, "official_publication", lambda r, n, c: f"{n} is certified")
    tree = _tree(root)
    assert _refit(root, *FIT) != 0
    assert "is certified" in capsys.readouterr().err
    assert _tree(root) == tree


def test_r2_3_a_rollback_after_a_crash_between_the_index_delete_and_reset_at_reindexes(held_gb, card,
                                                                                     monkeypatch):
    """The reviewer's probe: entries gone, `reset_at` not saved. The rollback
    empties the index marks whatever `reset_at` says: indexed again, never
    marked indexed with nothing in the index."""
    root, old, held = held_gb
    dense = _dense_uid(root)
    native = int(_marks(root)[dense][0])
    real = refit_mod._drop_index_entries

    def drop_then_crash(coll, win):
        n = real(coll, win)
        raise _Boom("after the index delete, before reset_at")
    monkeypatch.setattr(refit_mod, "_drop_index_entries", drop_then_crash)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_drop_index_entries", real)
    assert not refit_mod.read_marker(root, NAME)["steps"]["switch"].get("reset_at")
    assert _index_count(root, native) == 0
    assert _refit(root, extra=("--rollback",)) == 0
    assert _row(root, dense, "fts_done", "fts_version")[0] == 0, "the worker indexes it again"
    assert col.read(root, NAME).collection_id == old.collection_id and _held(root, sorted(held)) == held


@pytest.mark.parametrize("where", ["named", "repin", "staging"])
def test_r2_a_rollback_after_a_crash_inside_the_switch(held_gb, card, monkeypatch, where):
    root, old, held = held_gb
    before, text, snap0 = _marks(root), (root / f"{NAME}.toml").read_text(), _snapshot(root)
    real = refit_mod._save

    def save(r, n, m):
        real(r, n, m)
        if (m["steps"].get("switch") or {}).get(where):
            raise _Boom(f"after switch {where}")
    monkeypatch.setattr(refit_mod, "_save", save)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_save", real)
    assert _refit(root, extra=("--rollback",)) == 0
    assert col.read(root, NAME).collection_id == old.collection_id and _pin(root) == old.space_id
    assert _marks(root) == _reindexed(before, _dense_uid(root)) and _held(root, sorted(held)) == held
    assert (root / f"{NAME}.toml").read_text() == text and _snapshot(root).hash == snap0.hash


def test_r2_a_real_tantivy_lock_held_by_another_process_asks_for_a_stop_and_the_writer_is_free_after(
        held_gb, card, capsys):
    import subprocess
    import sys
    import tantivy
    root, old, _held_ = held_gb
    dense = _dense_uid(root)
    groups = [g for g in (root / NAME / "index").iterdir() if (g / "meta.json").is_file()]
    from app import fts
    coll = _local(root, NAME)
    native = int(_marks(root)[dense][0])
    group = next(g for g in groups if fts.books_held(g.name, [native], coll))
    holder = subprocess.Popen([sys.executable, "-I", "-c",
                               "import sys, time, tantivy\n"
                               "w = tantivy.Index.open(sys.argv[1]).writer()\n"
                               "print('held', flush=True)\n"
                               "time.sleep(120)\n", str(group)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert _refit(root, *FIT) == 3
        assert "louise node stop" in capsys.readouterr().err
        assert not refit_mod.read_marker(root, NAME)["steps"].get("switch"), "nothing of the switch written"
    finally:
        holder.kill()
        holder.wait()
    assert _refit(root, *FIT) == 0
    w = tantivy.Index.open(str(group)).writer()            # this process let it go
    w.wait_merging_threads()


def test_r2_7_a_document_added_before_the_switch_makes_the_change_set_stale(held_gb, monkeypatch):
    root, old, held = held_gb
    real = refit_mod._save

    def stop_after_changes(r, n, m):
        real(r, n, m)
        if (m["steps"].get("changes") or {}).get("done") and not m["steps"].get("switch"):
            raise _Boom("after changes")
    monkeypatch.setattr(refit_mod, "_save", stop_after_changes)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_save", real)
    _set(root, "INSERT INTO documents(uid, source, native_id, path, lang, in_scope) "
               "VALUES('gutenberg:99999', 'gutenberg', '99999', 'nowhere', 'en', 1)")
    logs: list[str] = []
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "resume")
    try:
        m = refit_mod.run(root, NAME, louise._layers_with(old, louise._layer_params(list(FIT))), log=logs.append)
    finally:
        lk.release()
    assert any("stale" in line and "last rowid moved" in line for line in logs)
    assert m["done"] and m["steps"]["changes"]["top"] == int(_row(root, "gutenberg:99999", "rowid")[0])


def test_r2_the_parts_and_snapshot_are_a_fresh_build_of_the_new_codex(held_gb, card, tmp_path, corpus_dir):
    """As pin-run's tests do: the refit's parts and snapshot entries are what
    a build of the new Codex from scratch writes, byte for byte."""
    from app.hold import records as rec, snapshot as sn
    root, old, _held_ = held_gb
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
    fs = sn.create(fresh_root, NAME, snapshot_id="2026-08", log=quiet)
    mine = _snapshot(root)
    a = {(s.slice, int(s.n)): s.to_dict() for s in mine.shards}
    b = {(s.slice, int(s.n)): s.to_dict() for s in fs.shards}
    assert a == b, "every entry, root and writer included"
    for s in fs.shards:
        assert (root / NAME / "shard" / s.path).read_bytes() == (fresh_root / NAME / "shard" / s.path).read_bytes()


def test_r2_4_a_refit_predecessor_ranks_as_the_target_itself_against_a_sisters_twin(held_gb, card, monkeypatch):
    """The tie: gutenberg after its refit, and a sister whose carry names the
    old id, both twins at the same snapshot. The refit is the target itself."""
    from types import SimpleNamespace
    from app.hold import annotations as ann
    from app.share import seeder
    root, old, _held_ = held_gb
    assert _refit(root, *FIT) == 0
    sis = root / "aa-sister" / "state"
    sis.mkdir(parents=True)
    (sis / "carry.json").write_text(json.dumps({"done": True, "from": old.collection_id, "to": "x"}))
    real_held = col._held
    a_sister = SimpleNamespace(collection_id="sha256:" + "ab" * 32, annotates=None, plugin="gutenberg")
    monkeypatch.setattr(col, "_held", lambda r: [("aa-sister", a_sister), *real_held(r)])
    monkeypatch.setattr(seeder, "current_snapshot", lambda d: SimpleNamespace(snapshot_id="2026-08"))
    assert ann._target_holder(root, old.collection_id)[0] == NAME
    fake_ann = SimpleNamespace(annotates=(SimpleNamespace(collection_id=old.collection_id),))
    assert col._target_names(root, fake_ann) == [NAME]


def test_r2_4_rename_carries_the_refit(held_gb, card):
    from app.hold import rename as rename_mod
    root, old, held = held_gb
    assert _refit(root, *FIT) == 0
    rename_mod.rename(root, NAME, "gb2")
    m = refit_mod.read_marker(root, "gb2")
    sa, ta = refit_mod.asides(root, "gb2", m)
    assert sa.is_dir() and ta.is_dir() and str(root / "gb2") in str(sa)
    assert refit_mod.predecessors(root / "gb2" / "state") == (old.collection_id,)
    lk = lock_mod.take(lock_mod.state_dir(root, "gb2"), "gb2", "test")
    try:
        refit_mod.rollback(root, "gb2", log=quiet)
    finally:
        lk.release()
    assert col.read(root, "gb2").collection_id == old.collection_id
    assert (root / "gb2.toml").read_text().count(old.collection_id) == 1, "its own file, under its new name"


def test_r2_5_a_stop_in_the_reset_loop_is_honoured_after_a_window_and_resumes(held_gb, monkeypatch):
    """SIGTERM inside the switch's reset: honoured after the window in hand
    is saved, resumed from `reset_at`."""
    import threading
    root, old, held = held_gb
    real = refit_mod._save

    def stop_after_changes(r, n, m):
        real(r, n, m)
        if (m["steps"].get("changes") or {}).get("done") and not m["steps"].get("switch"):
            raise _Boom("after changes")
    monkeypatch.setattr(refit_mod, "_save", stop_after_changes)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_save", real)
    # a second document in the change set: reset and redone by the book
    m = refit_mod.read_marker(root, NAME)
    ta = refit_mod.asides(root, NAME, m)[1]
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    row = con.execute("SELECT rowid, uid, native_id FROM documents WHERE uid != ? ORDER BY rowid LIMIT 1",
                      (_dense_uid(root),)).fetchone()
    con.close()
    changed = sorted(refit_mod.read_changed(ta / "changed.jsonl") + [[int(row[0]), row[1], int(row[2])]])
    refit_mod._write_changed(ta / "changed.jsonl", changed)
    stop = threading.Event()
    real_unversion = refit_mod._unversion

    def unversion(con, win):
        stop.set()                                   # SIGTERM arrives during the first window
        return real_unversion(con, win)
    monkeypatch.setattr(refit_mod, "_unversion", unversion)
    layers = louise._layers_with(old, louise._layer_params(list(FIT)))
    lk = lock_mod.take(lock_mod.state_dir(root, NAME), NAME, "test")
    try:
        m = refit_mod.run(root, NAME, layers, window=1, stop=stop, log=quiet)
        assert m.get("stopped") and refit_mod.read_marker(root, NAME)["steps"]["switch"]["reset_at"] == 1
        monkeypatch.setattr(refit_mod, "_unversion", real_unversion)
        m = refit_mod.run(root, NAME, layers, window=1, log=quiet)
    finally:
        lk.release()
    assert m["done"] and m["steps"]["switch"]["reset_at"] == 2
    assert all(_held(root, [c[2]])[c[2]] == {} for c in changed), "both reset"


def test_r2_6_a_missing_staging_build_is_refused_in_the_plan_and_before_the_pass(held_gb, capsys):
    from app.hold import records as rec
    root, old, _held_ = held_gb
    build = root / NAME / rec.STATE / rec.BUILD
    build.rename(build.with_name("build-away"))
    tree = _tree(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) != 0
    assert "no staging build database" in capsys.readouterr().err
    assert _refit(root, *FIT) != 0
    assert "no staging build database" in capsys.readouterr().err
    assert refit_mod.read_marker(root, NAME) is None or not refit_mod.read_marker(root, NAME)["steps"].get(
        "switch")
    assert {k: v for k, v in _tree(root).items() if "refit" not in k and ".replaced" not in k} == tree


def test_r2_8_retarget_recreates_the_annotations_against_the_refit_collection(held_gb, card, capsys):
    from app.hold import annotations as ann, retarget as rt
    from tests.hold.test_annotations import a_claim, claims_file
    root, old, _held_ = held_gb
    uids = sorted(_marks(root))
    codex, _snap = ann.create(root, "gb-editions", target_ref=NAME, from_snapshot="2026-08",
                              claims=claims_file(a_claim(uids[0], uids[1], subject_lang="en")),
                              platform_image=PLATFORM, snapshot_id="2026-09", key=KEY, now=NOW,
                              nonce="0f" * 16, log=lambda m: None)
    assert _refit(root, *FIT) == 0
    assert "`louise hold annotations retarget gb-editions`" in capsys.readouterr().out, "the refit's last line"
    _, new = col.resolve(root, NAME)
    assert rt.plan(root, "gb-editions")["due"]
    assert louise.main(["hold", "annotations", "retarget", "gb-editions", "--root", str(root),
                        "--platform-image", PLATFORM]) == 0
    _, now_ann = col.resolve(root, "gb-editions")
    assert [t.collection_id for t in cx.targets_of(now_ann)] == [new.collection_id]
    assert not [d.name for d in root.iterdir() if d.name.startswith("gb-editions.retargeted-")], "not held"
    (kept,) = (root / "gb-editions" / "state" / ".replaced").glob("*/collection")
    assert cx.loads((kept / "shard" / "codex.json").read_bytes()).collection_id == codex.collection_id, \
        "the old one kept, tucked away"
    assert not list(root.glob("gb-editions.retargeted-*.toml")), "its file set aside: the node no longer loads it"
    assert (root / "gb-editions.toml").is_file()
    from app.hold import seed
    assert sum(s.records for s in seed.latest_snapshot(root, "gb-editions").shards) == 1
    assert not rt.plan(root, "gb-editions")["due"], "nothing more to do"


def test_r2_8_retarget_is_a_route_of_the_tree():
    from app import api_commands as ac
    path, p = ac.resolve(["hold", "annotations", "retarget"])
    assert ac.argv_for(path, p, {"collection": "gutenberg-book-editions", "dry_run": True}) == \
        ["hold", "annotations", "retarget", "--dry-run", "gutenberg-book-editions"]


# ---- the third read of 39f352f0: retarget -----------------------------------------

def _editions(root):
    """gutenberg-book-editions' stand-in: claims about gb, naming its id."""
    from app.hold import annotations as ann
    from tests.hold.test_annotations import a_claim, claims_file
    uids = sorted(_marks(root))
    codex, _snap = ann.create(root, "gb-editions", target_ref=NAME, from_snapshot="2026-08",
                              claims=claims_file(a_claim(uids[0], uids[1], subject_lang="en")),
                              platform_image=PLATFORM, snapshot_id="2026-09", key=KEY, now=NOW,
                              nonce="0f" * 16, log=lambda m: None)
    return codex


def _retarget(root, *extra):
    return louise.main(["hold", "annotations", "retarget", "gb-editions", "--root", str(root),
                        "--platform-image", PLATFORM, *extra])


def _retargeted(root, new):
    _, now_ann = col.resolve(root, "gb-editions")
    assert [t.collection_id for t in cx.targets_of(now_ann)] == [new.collection_id]
    from app.hold import seed
    assert seed.latest_snapshot(root, "gb-editions").collection_id == now_ann.collection_id
    return now_ann


def test_r3_1_the_file_is_set_aside_in_its_own_directory_never_across_filesystems(held_gb, card, tmp_path,
                                                                                 monkeypatch):
    """On the node /config is not /data: the collection files live apart from
    the collections, and a move from one to the other is EXDEV."""
    import errno
    from app.hold import deposit
    root, old, _held_ = held_gb
    cfg = tmp_path / "config-collections"
    cfg.mkdir()
    for p in root.glob("*.toml"):
        p.rename(cfg / p.name)
    monkeypatch.setattr(deposit, "held_file", lambda r, n: cfg / f"{n}.toml")
    real = os.replace

    def replace(src, dst):
        a, b = Path(src).resolve(), Path(dst).resolve()
        if (cfg in a.parents) != (cfg in b.parents):
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    _editions(root)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert _retarget(root) == 0
    _retargeted(root, new)
    assert (cfg / "gb-editions.toml").is_file()
    assert [p.name for p in cfg.glob(".gb-editions.retargeted-*.aside")], "the old file set aside in its directory"
    assert not list(cfg.glob("gb-editions.retargeted-*.toml")), "and not loaded"


def test_r3_1_a_rerun_after_the_old_was_renamed_takes_no_stray_lock_and_finishes(held_gb, card, monkeypatch):
    from app.hold import retarget as rt
    root, old, _held_ = held_gb
    _editions(root)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    real = rt.rename_resumed
    seen = {"n": 0}

    def crash_after_out(r, a, b):
        got = real(r, a, b)
        seen["n"] += 1
        if seen["n"] == 1:
            raise _Boom("after the old was renamed")
        return got
    monkeypatch.setattr(rt, "rename_resumed", crash_after_out)
    with pytest.raises(_Boom):
        _retarget(root)
    assert not (root / "gb-editions").exists(), "the name holds nothing now"
    monkeypatch.setattr(rt, "rename_resumed", real)
    assert _retarget(root) == 0
    _retargeted(root, new)


def test_r3_2_a_create_stopped_in_its_parts_is_resumed_and_its_snapshot_required(held_gb, card, monkeypatch):
    from app.hold import annotations as ann
    root, old, _held_ = held_gb
    _editions(root)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    real = ann.write_snapshot
    seen = {"n": 0}

    def stopped(*a, **k):
        seen["n"] += 1
        if seen["n"] == 1:
            raise _Boom("SIGINT in the annotations' snapshot")
        return real(*a, **k)
    monkeypatch.setattr(ann, "write_snapshot", stopped)
    with pytest.raises(_Boom):
        _retarget(root)
    assert (root / "gb-editions.retarget" / "shard" / "codex.json").is_file(), "a Codex and no snapshot"
    assert _retarget(root) == 0
    _retargeted(root, new)


@pytest.mark.parametrize("side", ["out", "in"])
def test_r3_3_a_kill_between_renames_folder_move_and_file_move_is_finished(held_gb, card, monkeypatch, side):
    from app.hold import rename as rename_mod
    root, old, _held_ = held_gb
    _editions(root)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    real = rename_mod._move
    seen = {"files": 0}

    def move(src, dst):
        if str(dst).endswith(".toml"):
            seen["files"] += 1
            if seen["files"] == (1 if side == "out" else 2):
                raise _Boom(f"killed between the {side} rename's folder and its file")
        return real(src, dst)
    monkeypatch.setattr(rename_mod, "_move", move)
    with pytest.raises(_Boom):
        _retarget(root)
    monkeypatch.setattr(rename_mod, "_move", real)
    assert _retarget(root) == 0
    _retargeted(root, new)
    assert (root / "gb-editions.toml").is_file() and not (root / "gb-editions.retarget.toml").exists()


def test_r3_4_the_retired_copy_is_not_held_and_no_cascade_reads_it(held_gb, card):
    from app.hold import cascade
    root, old, _held_ = held_gb
    _editions(root)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert _retarget(root) == 0
    names = [n for n, _c in col._held(root)]
    assert not [n for n in names if ".retargeted-" in n], names
    cascade.reset()
    decl = cascade.declarations(root)
    assert set(decl.sources) == {"gb-editions"}, decl.sources
    assert cascade.annotators(root, new.collection_id) == ["gb-editions"]
    assert refit_mod.annotations_naming(root, NAME, old.collection_id) == []
    tucked = list((root / "gb-editions" / "state" / ".replaced").glob("*/collection/shard/codex.json"))
    assert len(tucked) == 1, "kept, not deleted"


def test_r3_5_a_refit_of_a_collection_with_no_snapshot_attests_nothing_and_finishes(held_gb, card, tmp_path):
    root, old, _held_ = held_gb
    snaps = root / NAME / "shard" / "snapshots"
    snaps.rename(tmp_path / "snapshots-away")                 # a collection that froze none
    assert _refit(root, *FIT) == 0
    m = refit_mod.read_marker(root, NAME)
    assert m["done"] and m["steps"]["attest"]["parts"] == [] and "no snapshot" in m["steps"]["attest"]["why"]


def test_r3_6_retarget_waits_for_the_refit_to_be_gated(held_gb, card, monkeypatch, capsys):
    root, old, _held_ = held_gb
    _editions(root)
    monkeypatch.setattr(louise, "cmd_embedder_cpu_reference", lambda a: 1)
    assert _refit(root, *FIT) == 1
    capsys.readouterr()
    assert _retarget(root) != 0
    assert "gate has not passed" in capsys.readouterr().err
    assert col.read(root, "gb-editions").annotates[0].collection_id == old.collection_id


def test_r3_6_a_rollback_is_refused_while_a_retarget_names_the_new_id_and_undo_is_the_way_back(held_gb, card,
                                                                                               capsys):
    root, old, _held_ = held_gb
    ed = _editions(root)
    assert _refit(root, *FIT) == 0
    assert _retarget(root) == 0
    capsys.readouterr()
    assert _refit(root, extra=("--rollback",)) != 0
    err = capsys.readouterr().err
    assert "gb-editions" in err and "--undo" in err
    assert _retarget(root, "--undo") == 0
    assert col.read(root, "gb-editions").collection_id == ed.collection_id, "the old one back"
    assert not [n for n, _c in col._held(root) if ".retarget" in n]
    assert _refit(root, extra=("--rollback",)) == 0
    assert col.read(root, NAME).collection_id == old.collection_id


def test_r3_7_node_rows_set_aside_and_not_yet_recorded_come_back(held_gb, card, monkeypatch):
    from app import node_state
    root, old, _held_ = held_gb
    _published_fixture(root, old)
    real = refit_mod._node_rows_aside

    def aside_then_crash(*a, **k):
        real(*a, **k)
        raise _Boom("after the node rows went, before the marker said so")
    monkeypatch.setattr(refit_mod, "_node_rows_aside", aside_then_crash)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_node_rows_aside", real)
    assert _refit(root, extra=("--rollback",)) == 0
    n = sqlite3.connect(node_state.path())
    assert n.execute("SELECT value FROM control WHERE key=?", (f"publish:{NAME}",)).fetchone() == ("2026-11-01 pass",)
    n.close()


def test_r3_8_the_retargeted_annotations_leave_quarantine_by_their_own_attested_roots(held_gb, card, signer):
    from app import copy_state
    from app.hold import envelope as env
    root, old, _held_ = held_gb
    _editions(root)
    assert _refit(root, *FIT) == 0
    assert _retarget(root) == 0
    _, ann_codex = col.resolve(root, "gb-editions")
    copy_state.reset_cache()
    got = copy_state.evaluate(root, "gb-editions", ann_codex, licence="not-required",
                              key=env.key_text(KEY.verify_key), before=False)
    assert {t.name: t.status for t in got.tests}["roots"] == copy_state.PASSED, got.reasons


def test_r3_9_retarget_takes_the_refits_successor_over_a_sister_with_a_newer_snapshot(held_gb, card, monkeypatch):
    from types import SimpleNamespace
    from app.hold import annotations as ann, retarget as rt
    from app.share import seeder
    root, old, _held_ = held_gb
    assert _refit(root, *FIT) == 0
    sis = root / "aa-sister" / "state"
    sis.mkdir(parents=True)
    (sis / "carry.json").write_text(json.dumps({"done": True, "from": old.collection_id, "to": "x"}))
    a_sister = SimpleNamespace(collection_id="sha256:" + "ab" * 32, annotates=None, plugin="gutenberg")
    real_held = col._held
    monkeypatch.setattr(col, "_held", lambda r: [("aa-sister", a_sister), *real_held(r)])
    monkeypatch.setattr(seeder, "current_snapshot", lambda d: SimpleNamespace(
        snapshot_id="2026-12" if "aa-sister" in str(d) else "2026-08"))
    assert ann._target_holder(root, old.collection_id)[0] == "aa-sister", "the newest snapshot wins there"
    assert rt.holder_of(root, old.collection_id)[0] == NAME, "the refit's successor wins here"


def test_r3_10_a_rollback_of_an_unfinished_refit_refuses_once_another_wrote(held_gb, card, monkeypatch, capsys):
    from app import embeddings
    root, old, held = held_gb
    real = refit_mod._save

    def stop_after_switch(r, n, m):
        real(r, n, m)
        if (m["steps"].get("switch") or {}).get("done"):
            raise _Boom("after the switch")
    monkeypatch.setattr(refit_mod, "_save", stop_after_switch)
    _first(root, old)
    monkeypatch.setattr(refit_mod, "_save", real)
    _, new = col.resolve(root, NAME)
    some = sorted(b for b in held if b != int(_marks(root)[_dense_uid(root)][0]))[0]
    embeddings.upsert_chunks([{"id": f"gutenberg:{some}:77:1", "book_id": some, "lang": "en", "seq": 77,
                               "chapter": "", "text": "written meanwhile", "vector": np.ones(new.space.dim, "float32")}],
                             collection=_local(root, NAME), dtype=new.space.dtype)
    capsys.readouterr()
    assert _refit(root, extra=("--rollback",)) != 0
    assert "other rows than the switch left" in capsys.readouterr().err
    assert col.read(root, NAME).collection_id == new.collection_id, "nothing changed"
    assert _held(root, [some])[some].get(f"gutenberg:{some}:77:1") == "written meanwhile", "never dropped"
