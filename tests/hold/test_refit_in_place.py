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
    """Every file under the collection and its file, by path: (size, mtime)."""
    out = {}
    for p in sorted([*(root / NAME).rglob("*"), root / f"{NAME}.toml"]):
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
    assert "1 written again" in out and "set aside" in out
    assert _tree(root) == tree, "not a byte written"
    assert col.read(root, NAME).collection_id == old.collection_id
    assert refit_mod.read_marker(root, NAME) is None


def test_the_rollback_puts_back_the_codex_the_pin_the_rows_the_marks_and_the_file(held_gb, card):
    root, old, held = held_gb
    before, text, snap0 = _marks(root), (root / f"{NAME}.toml").read_text(), _snapshot(root)
    assert _refit(root, *FIT) == 0
    assert _refit(root, extra=("--rollback",)) == 0
    assert col.read(root, NAME).collection_id == old.collection_id
    assert _pin(root) == old.space_id
    assert _marks(root) == before
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
    assert _marks(root) == before and _held(root, sorted(held)) == held and _pin(root) == old.space_id
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
