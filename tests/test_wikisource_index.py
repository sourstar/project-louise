"""tests/test_wikisource_index.py — a run's works index
(app/plugins/wikisource/index.py): every page by namespace and title with
its stream, built once in parallel, fresh by the dump's own signature."""
from __future__ import annotations

import os

from app.plugins.wikisource import index
from tests.wikisource_fixture import write_run


def test_the_index_holds_every_page_with_its_stream_and_the_namespaces(tmp_path, isolated_config):
    dump = write_run(tmp_path)
    w = index.Works(index.ensure(dump))
    assert w.namespaces[108] == "Pagina" and w.ns_named("Autore") == 102
    assert w.main_titles() == ["Vita", "Vita/Libro primo", "Vita/Libro secondo"], "no redirect"
    assert w.subpages("Vita") == ["Vita/Libro primo", "Vita/Libro secondo"]
    p = w.page(108, "Pagina:Vita.djvu/6")
    assert (p["id"], p["rev_id"], p["sha1"], p["model"]) == (22, 220, "s22", "proofread-page")
    with open(dump, "rb") as f:
        f.seek(p["start"])
        import bz2
        assert b"Pagina:Vita.djvu/6" in bz2.decompress(f.read(p["end"] - p["start"]))
    assert w.last_page(108, "Pagina:Vita.djvu") == 7
    assert w.page(0, "Vita/Libro primo")["tags"] == '["<pages index=\\"Vita.djvu\\" from=5 to=6 />"]'
    assert w.by_id(33)["title"] == "Vita/Libro secondo"


def test_it_is_built_once_and_again_when_the_dump_moves(tmp_path, isolated_config, monkeypatch):
    dump = write_run(tmp_path)
    built = []
    real = index.build
    monkeypatch.setattr(index, "build", lambda *a, **k: built.append(1) or real(*a, **k))
    first = index.ensure(dump)
    assert index.ensure(dump) == first and built == [1], "fresh: not built again"
    st = dump.stat()
    os.utime(dump, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    index.ensure(dump)
    assert built == [1, 1], "the dump moved: built again"


def test_it_lives_in_the_cache_when_one_is_named(tmp_path, isolated_config, monkeypatch):
    from app import config, derived
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    dump = write_run(tmp_path)
    p = index.ensure(dump)
    assert p.parent == derived.place(index.KIND, dump)
