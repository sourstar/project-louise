"""app/display_names.py — the name a page shows for a collection, apart from
its folders (operator, 2026-11-13: "the name tag shall be independent from the
folders naming"; "some univoque (post fix?) tag shall be added" for sisters;
"No, chunking may differ"). The rules, the card's rename (the file's `title`
only, no folder moved), and the CLI finding a collection by that name."""
import pytest

from app import collections as collections_mod, display_names as dn
from app.hold import collection as col
from app.hold.codex import CodexError

H1 = "sha256:" + "3f2a1c" + "0" * 58
H2 = "sha256:" + "9b1e02" + "1" * 58
H3 = "sha256:" + "3f2a1c" + "2" * 58       # agrees with H1 on six characters


def E(key, cid=None, file_title=None, codex_title=None):
    return dn.Entry(key, cid, file_title, codex_title)


def test_the_name_is_the_files_title_else_the_codexs_short_title_else_the_key():
    got = dn.labels([
        E("normattiva", H1, None, "Normattiva — the laws of the Italian State, every version"),
        E("gutenberg", H2, "Gutenberg", "Project Gutenberg — public-domain books"),
        E("bare", None, None, None),
        E("blank-title", H3, "   ", "EUR-Lex — the law of the European Union"),
    ])
    assert got == {"normattiva": "Normattiva", "gutenberg": "Gutenberg", "bare": "bare",
                   "blank-title": "EUR-Lex"}


def test_sisters_that_share_a_name_are_told_apart_by_their_collection_id_whatever_differs():
    """The postfix does not depend on WHAT differs (encoder, chunking, layers):
    it is the collection_id's shortest distinguishing prefix, six at least
    (Git's abbreviated object names)."""
    title = "Normattiva — the laws of the Italian State, every version"
    got = dn.labels([E("normattiva", H1, None, title), E("normattiva-dar", H2, None, title),
                     E("legi", "sha256:" + "a" * 64, None, "LEGI — the consolidated French law")])
    assert got == {"normattiva": "Normattiva · 3f2a1c", "normattiva-dar": "Normattiva · 9b1e02",
                   "legi": "LEGI"}
    # a third sister agreeing on six characters: those two grow, the other keeps its six
    got = dn.labels([E("normattiva", H1, None, title), E("normattiva-dar", H2, None, title),
                     E("normattiva-3", H3, None, title)])
    assert got["normattiva"] == "Normattiva · 3f2a1c0" and got["normattiva-3"] == "Normattiva · 3f2a1c2"
    assert len(set(got.values())) == 3
    # the operator's own title replaces the postfix where it is unique; compared without case
    got = dn.labels([E("normattiva", H1, None, title), E("normattiva-dar", H2, "Normattiva DAR", title)])
    assert got == {"normattiva": "Normattiva", "normattiva-dar": "Normattiva DAR"}
    got = dn.labels([E("a", H1, "PubMed", None), E("b", H2, "pubmed", None)])
    assert got == {"a": "PubMed · 3f2a1c", "b": "pubmed · 9b1e02"}


def test_names_stay_unique_whatever_the_titles_say():
    """A title written like another's postfixed name: the keys tell them apart."""
    got = dn.labels([E("a", H1, None, "X"), E("b", H2, None, "X"), E("c", H3, "X · 3f2a1c", None)])
    assert len({v.casefold() for v in got.values()}) == 3, got


def test_a_name_resolves_to_its_keys():
    title = "Normattiva — the laws of the Italian State"
    entries = [E("normattiva", H1, None, title), E("normattiva-dar", H2, None, title),
               E("gutenberg", H3, "Gutenberg", None)]
    assert dn.resolve("gutenberg", entries) == ["gutenberg"]
    assert dn.resolve("normattiva · 9B1E02", entries) == ["normattiva-dar"]
    assert sorted(dn.resolve("Normattiva", entries)) == ["normattiva", "normattiva-dar"], "shared: both"
    assert dn.resolve("nothing", entries) == []


@pytest.fixture
def node(tmp_path, monkeypatch):
    from tests.hold.test_annotations import a_target, make
    root = tmp_path / "louise" / "collections"
    root.mkdir(parents=True)
    (tmp_path / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    a_target(root)
    make(root)
    dn.invalidate()
    yield root
    dn.invalidate()


def test_the_cli_finds_a_collection_by_the_name_a_page_shows(node):
    from app import collection_file
    root = node
    (c,) = collections_mod.load_annotations()
    collection_file.set_values(c.name, {"title": "Same works"})
    assert col.resolve(root, "same works")[0] == c.name
    assert col.resolve(root, c.name)[0] == c.name, "the local name, as always"
    with pytest.raises(CodexError, match="the name a page shows"):
        col.resolve(root, "No such name")
