"""A reference source finds its plugin's corpus by the collections held, not
by a folder name (München, 2026-11-13: the operator renamed legi → legi_F
and neuris → NeuRIS; a rename moves a collection's origin with its name, so
`<src>/legi` no longer named legi's corpus and the next `refs-sync` would
have found none — a lookup by name, where CLAUDE.md keys bookkeeping on
physical identity)."""
import os
from types import SimpleNamespace

from app import refs
from app.plugins.base import RefSyncState
from app.refs import legi as lref, store as refs_store
from tests.test_refs_legi import _codes, _forget_indexes
from tests.test_refs_legi import tarball


def _held(name, plugin, corpus_dir, source="origin"):
    return SimpleNamespace(name=name, plugin=plugin, corpus_dir=corpus_dir, source=source)


def test_held_corpora_by_plugin_once_per_physical_directory(tmp_path):
    shared = tmp_path / "corpora" / "normattiva"
    shared.mkdir(parents=True)
    (tmp_path / "corpora" / "legi_F").mkdir()
    old = tmp_path / "collections" / "normattiva_old"
    new = tmp_path / "collections" / "Normattiva"
    for d in (old, new):
        d.mkdir(parents=True)
        os.symlink(shared, d / "origin")              # one origin, two collections
    got = refs_store.held_corpora([
        _held("normattiva_old", "normattiva", old / "origin"),
        _held("Normattiva", "normattiva", new / "origin"),
        _held("legi_F", "legi", tmp_path / "corpora" / "legi_F"),
        _held("legi-peer", "legi", tmp_path / "nowhere"),                       # no origin on disk
        _held("legi-copy", "legi", tmp_path / "corpora" / "legi_F", source="shard"),   # a SHARD's: no origin
    ])
    assert got == {"normattiva": (shared.resolve(),), "legi": ((tmp_path / "corpora" / "legi_F").resolve(),)}


def test_a_renamed_collections_corpus_is_synced_from_where_it_is(tmp_path):
    corpus = tmp_path / "corpora" / "legi_F"                     # not named after the plugin
    tarball(corpus / "Freemium_legi_global_20250713-140000.tar.gz", _codes())
    src = tmp_path / "refs"
    src.mkdir()
    con = refs.connect(tmp_path / "refs.db")
    _forget_indexes()
    assert refs_store.sync_source(con, refs.REFS_REGISTRY["legi-refs"], RefSyncState(ref_root=src))["rows"] == 0, \
        "by the folder name alone there is nothing"
    got = refs_store.sync_all(con, src, corpora=refs_store.held_corpora([_held("legi_F", "legi", corpus)]))
    assert got["legi-refs"]["rows"] > 0
    store = refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])
    assert store.count("texte") > 0 and store.count("article") > 0
    _forget_indexes()


def test_a_plugin_no_collection_carries_is_read_from_its_folder_as_before(tmp_path):
    src = tmp_path / "refs"
    tarball(src / "legi" / "Freemium_legi_global_20250713-140000.tar.gz", _codes())
    state = RefSyncState(ref_root=src, corpora={"normattiva": (tmp_path,)})
    assert state.corpus_roots("legi") == (src / "legi",)
    assert lref.LegiRefSource.input_mtime(state) > 0
    assert RefSyncState(ref_root=tmp_path / "none").corpus_roots("legi") == ()
