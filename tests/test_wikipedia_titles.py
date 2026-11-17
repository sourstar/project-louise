"""tests/test_wikipedia_titles.py — a Wikipedia title answered from the node's
own wikipedia (app/wikipedia_titles.py), and "no page" only on proof
(2026-11-17, the node's dry run: 83 claims withdrawn). A title the manifest
misses is looked up in the RUN's own multistream index: a redirect the
manifest holds no row for is read from the run's dump and followed; an
article layer 1 holds under the archive's 2024 title is found by its page id;
a title the index does not list is "no page"; one it lists and the manifest
does not hold, or one no index on disk can tell, is UNKNOWN.

The fixture is the node's pattern, both layers: layer 1 (the archive's
parquet) holds "Bouvard et Pécuchet" (page 10, moved since to "Bouvard and
Pécuchet"), "Encyclopædia Britannica Eleventh Edition" (page 20, now "Eleventh
edition of the Encyclopædia Britannica"), "Journey to the Center of the
Earth" and "The Sleeper Awakes"; the run's dump holds those pages under
their current titles and the redirects to them, of which the manifest holds
some rows and not others."""
import bz2
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import db
from app import gutenberg_editions as ge
from app import wikipedia_titles as wt
from app.plugins.base import DiscoverState
from app.plugins.wikipedia import SOURCE, archive
from app.plugins.wikipedia import source as wsrc
from app.plugins.wikipedia.ids import DUMP_BASE, native_id_of
from tests import wikipedia_props

RUN = "20260801"
EN = "https://en.wikipedia.org/wiki/"

#: layer 1: (page, the archive's title, its text)
ARCHIVE = [(10, "Bouvard et Pécuchet", "A novel by Flaubert."),
           (20, "Encyclopædia Britannica Eleventh Edition", "An encyclopaedia."),
           (30, "Journey to the Center of the Earth", "A novel by Verne."),
           (40, "The Sleeper Awakes", "A novel by Wells.")]
#: the run's dump, every page: (page, title, text, redirect target)
RUN_PAGES = [(10, "Bouvard and Pécuchet", "A novel by Flaubert.", None),
             (11, "Bouvard et Pécuchet", "#REDIRECT [[Bouvard and Pécuchet]]", "Bouvard and Pécuchet"),
             (12, "Bouvard and Pecuchet", "#REDIRECT [[Bouvard and Pécuchet]]", "Bouvard and Pécuchet"),
             (20, "Eleventh edition of the Encyclopædia Britannica", "An encyclopaedia.", None),
             (30, "Journey to the Center of the Earth", "A novel by Verne.", None),
             (31, "Journey to the Centre of the Earth", "#REDIRECT [[Journey to the Center of the Earth]]",
              "Journey to the Center of the Earth"),
             (40, "The Sleeper Awakes", "A novel by Wells.", None),
             (41, "When the Sleeper Wakes", "#REDIRECT [[The Sleeper Awakes]]", "The Sleeper Awakes"),
             (50, "Café", "A place.", None),
             (51, "Cafe", "#REDIRECT [[Café]]", "Café"),
             (52, "AT&T", "A company.", None),
             (60, "Nowhere yet", "Held by the run, not read here.", None),
             (70, "Loop A", "#REDIRECT [[Loop B]]", "Loop B"),
             (71, "Loop B", "#REDIRECT [[Loop A]]", "Loop A"),
             (80, "Hop 1", "#REDIRECT [[Hop 2]]", "Hop 2"),
             (81, "Hop 2", "#REDIRECT [[Hop 3]]", "Hop 3"),
             (82, "Hop 3", "#REDIRECT [[Hop 4]]", "Hop 4"),
             (83, "Hop 4", "#REDIRECT [[Hop 5]]", "Hop 5"),
             (84, "Hop 5", "The end of the chain.", None)]
PROPS = [(10, "wikibase_item", "Q1"), (20, "wikibase_item", "Q2"), (30, "wikibase_item", "Q3"),
         (40, "wikibase_item", "Q4"), (50, "wikibase_item", "Q5"), (52, "wikibase_item", "Q6"),
         (60, "wikibase_item", "Q7"), (84, "wikibase_item", "Q8")]
#: the dump-layer pages the manifest holds rows for: not the redirects 11,
#: 31, 41, nor 60, nor the loop's and the chain's redirects (the node's
#: pattern); the chain's end, 84, is held
DUMP_ROWS = frozenset({12, 50, 51, 52, 84})


def _page(pid: int, title: str, text: str, redirect: str | None) -> str:
    red = f"<redirect title={quoteattr(redirect)} />" if redirect else ""
    return (f"<page><title>{escape(title)}</title><ns>0</ns><id>{pid}</id>{red}<revision><id>{pid}00</id>"
            f"<timestamp>2026-07-31T12:00:00Z</timestamp><text>{escape(text)}</text></revision></page>\n")


def a_run(corpus: Path, pages=RUN_PAGES, props=PROPS, *, per: int = 3, lang: str = "en") -> Path:
    """`<lang>wiki/<RUN>/`: the run's multistream dump, `per` pages a stream,
    its index — the titles written as the dump's <title> escapes them — and
    its page_props."""
    d = corpus / f"{lang}wiki" / RUN
    d.mkdir(parents=True)
    streams, lines, at = [], [], 0
    for i in range(0, len(pages), per):
        s = bz2.compress("".join(_page(*p) for p in pages[i:i + per]).encode())
        lines += [f"{at}:{p[0]}:{escape(p[1], {chr(39): '&#039;', chr(34): '&quot;'})}" for p in pages[i:i + per]]
        streams.append(s)
        at += len(s)
    dump = d / f"{lang}wiki-{RUN}-pages-articles-multistream.xml.bz2"
    dump.write_bytes(b"".join(streams))
    wt.index_path(dump).write_bytes(bz2.compress(("\n".join(lines) + "\n").encode()))
    wikipedia_props.write(dump, props)
    return dump


def an_archive(corpus: Path, rows=ARCHIVE) -> None:
    """Layer 1: the archive's parquet, one row an article (Upstash's shape)."""
    (corpus / "data" / "en").mkdir(parents=True)
    pq.write_table(pa.table({"id": [f"{p}_0" for p, _, _ in rows],
                             "url": [EN + t.replace(" ", "_") for _, t, _ in rows],
                             "title": [t for _, t, _ in rows], "text": [x for _, _, x in rows],
                             "embedding": [np.ones(4, dtype=np.float32).tolist() for _ in rows]}),
                   corpus / "data" / "en" / "000.parquet")


def a_manifest(path: Path, refs, *, dump_rows=DUMP_ROWS, pages=RUN_PAGES, archive_rows=ARCHIVE) -> Path:
    """The manifest as the node's holds it: every archive article under the
    ARCHIVE's title, with text; the dump-layer pages of `dump_rows` under
    their run's title, a redirect with no chunk."""
    title_of = {p[0]: (p[1], p[3] is None) for p in pages}
    archive_title = {p: t for p, t, _ in archive_rows}
    con = sqlite3.connect(path)
    con.executescript(db.SCHEMA)
    for r in refs:
        n = int(r.native_id)
        pid = n % 10 ** 10
        if n < DUMP_BASE:
            title, text = archive_title[pid], True
        elif pid in dump_rows:
            title, text = title_of[pid]
        else:
            continue
        con.execute("INSERT INTO documents(uid, source, native_id, path, title, fts_done, chunks) VALUES (?,?,?,?,?,1,?)",
                    (f"wikipedia:{n}", "wikipedia", str(n), r.locator, title, 2 if text else 0))
    con.commit()
    con.close()
    return path


@pytest.fixture
def wiki(tmp_path):
    """The node's wikipedia: its corpus (archive + the run), its manifest, the run's dumps."""
    wsrc._stream_cache.clear()
    archive._groups.clear()
    archive._layouts.clear()
    corpus = tmp_path / "wikipedia"
    an_archive(corpus)
    a_run(corpus)
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=corpus, snapshot=RUN)) if r.scope == "document"]
    assert {int(r.native_id) for r in refs if int(r.native_id) < DUMP_BASE} == {10, 20, 30, 40}, \
        "layer 1 holds the archive's four articles; the dump pass skips them"
    manifest = a_manifest(tmp_path / "wikipedia.db", refs)
    return SimpleNamespace(corpus=corpus, manifest=manifest, dumps=wt.run_dumps(corpus, RUN), tmp=tmp_path,
                           refs=refs)


def resolve(wiki, titles, **kw):
    kw.setdefault("dumps", wiki.dumps)
    kw.setdefault("run", RUN)
    return wt.resolve(wiki.manifest, [("en", t) for t in titles], log=lambda m: None, **kw)


def key(url: str) -> str:
    return ge.split_wiki(url)[1]


# ---- the node's cases --------------------------------------------------------------

def test_a_redirect_the_manifest_holds_no_row_for_is_read_from_the_runs_dump(wiki):
    """Journey and Sleeper: the redirect is a page of the run, absent from the
    manifest; its target is held (layer 1) under the same title."""
    got = resolve(wiki, [key(EN + "Journey_to_the_Centre_of_the_Earth"), key(EN + "When_the_Sleeper_Wakes")])
    j, s = got[("en", "Journey to the Centre of the Earth")], got[("en", "When the Sleeper Wakes")]
    assert (j.kind, j.item, j.redirects, j.run) == (wt.ITEM, "Q3", 1, RUN)
    assert (s.kind, s.item, s.redirects) == (wt.ITEM, "Q4", 1)
    assert j.page == "wikipedia:30", "the archive's row, layer 1"


def test_a_moved_article_layer_1_holds_under_its_old_title_is_found_by_its_page_id(wiki):
    """Bouvard and the Britannica: the run titles page 10 "Bouvard and
    Pécuchet", the manifest holds it as the archive did in 2024. Found by the
    run's index, by the page's id, the title the manifest holds said."""
    titles = [key(EN + "Bouvard_and_P%C3%A9cuchet"), key(EN + "Eleventh_edition_of_the_Encyclop%C3%A6dia_Britannica"),
              key(EN + "Bouvard_et_P%C3%A9cuchet"), key(EN + "Bouvard_and_Pecuchet")]
    assert titles[:2] == ["Bouvard and Pécuchet", "Eleventh edition of the Encyclopædia Britannica"], \
        "percent-decoded as UTF-8: é and æ, NFC"
    got = resolve(wiki, titles)
    b, e = got[("en", titles[0])], got[("en", titles[1])]
    assert (b.kind, b.item, b.code, b.stored) == (wt.ITEM, "Q1", wt.BY_PAGE_ID, "Bouvard et Pécuchet")
    assert (e.kind, e.item, e.code, e.stored) == (wt.ITEM, "Q2", wt.BY_PAGE_ID, "Encyclopædia Britannica Eleventh Edition")
    old = got[("en", "Bouvard et Pécuchet")]
    assert (old.item, old.code, old.redirects) == ("Q1", "", 0), "the manifest's own title: layer 1's row, directly"
    red = got[("en", "Bouvard and Pecuchet")]
    assert (red.item, red.code, red.redirects) == ("Q1", wt.BY_PAGE_ID, 1), \
        "a redirect row the manifest holds, to the run's title it does not: by page id"


def test_non_ascii_titles_are_matched_in_both_layers_as_nfc(wiki):
    """é and æ through the whole path: the URL percent-decoded, normalised
    (NFC), hashed as the manifest stores the title (raw UTF-8, never
    normalised), in layer 1 and in the dump layer."""
    idx = wt.index(wiki.manifest, log=lambda m: None)
    assert idx.rowids(0, "Bouvard et Pécuchet") and idx.rowids(0, "Café"), "both layers' titles, as stored"
    got = resolve(wiki, [key(EN + "Caf%C3%A9"), "Café", "Cafe", key(EN + "Bouvard_et_P%C3%A9cuchet")], idx=idx)
    assert got[("en", "Café")].item == got[("en", "Café")].item == "Q5", "NFD in the URL: NFC before the match"
    assert (got[("en", "Cafe")].item, got[("en", "Cafe")].redirects) == ("Q5", 1)
    assert got[("en", "Bouvard et Pécuchet")].item == "Q1"


def test_a_title_in_neither_the_manifest_nor_the_runs_index_is_no_page(wiki):
    got = resolve(wiki, ["Nowhere at all"])[("en", "Nowhere at all")]
    assert (got.kind, got.code, got.run) == (wt.NONE, wt.NO_PAGE, RUN)
    assert "index" in got.why


def test_a_page_the_run_holds_and_the_manifest_does_not_is_unknown(wiki):
    got = resolve(wiki, ["Nowhere yet"])[("en", "Nowhere yet")]
    assert (got.kind, got.code) == (wt.UNKNOWN, wt.NOT_READ), "held by the run, not read yet: never 'no page'"


def test_without_the_runs_dump_or_index_a_missing_title_is_unknown(wiki):
    titles = ["Nowhere at all", "Journey to the Centre of the Earth", "Café"]
    none = resolve(wiki, titles, dumps={})
    assert {t: (r.kind, r.code) for (_, t), r in none.items()} == {
        "Nowhere at all": (wt.UNKNOWN, wt.NO_DUMP), "Journey to the Centre of the Earth": (wt.UNKNOWN, wt.NO_DUMP),
        "Café": (wt.ITEM, "")}, "what the manifest holds still answers"
    dump = wiki.dumps["en"]
    ix = wt.index_path(dump)
    saved = ix.read_bytes()
    ix.unlink()
    assert resolve(wiki, titles[:1])[("en", titles[0])].code == wt.NO_DUMP, "no index on disk"
    ix.write_bytes(saved[: len(saved) // 2])
    cut = resolve(wiki, titles[:1])[("en", titles[0])]
    assert (cut.kind, cut.code) == (wt.UNKNOWN, wt.UNREADABLE), "an index cut short proves nothing"
    ix.write_bytes(saved)
    dump.rename(dump.with_name("gone.bz2"))
    assert resolve(wiki, titles[:1])[("en", titles[0])].code == wt.NO_DUMP, "no dump on disk"
    assert wt.run_dumps(wiki.corpus, RUN) == {} and wt.run_dumps(wiki.corpus, "20260901") == {}


def test_the_redirect_loop_and_max_hops_still_hold_through_the_index(wiki):
    got = resolve(wiki, ["Loop A", "Hop 1", "Hop 2"])
    assert (got[("en", "Loop A")].kind, got[("en", "Loop A")].code) == (wt.NONE, wt.LOOP)
    assert (got[("en", "Hop 1")].kind, got[("en", "Hop 1")].code) == (wt.NONE, wt.LOOP), \
        f"four redirects: longer than MAX_HOPS ({wt.MAX_HOPS})"
    assert (got[("en", "Hop 2")].item, got[("en", "Hop 2")].redirects) == ("Q8", wt.MAX_HOPS)


def test_an_escaped_index_title_is_found_as_the_title(wiki):
    """`AT&T` written `AT&amp;T` in the index (as the dump's <title> holds it):
    found either way."""
    assert {"AT&T", "AT&amp;T"} <= wt.index_forms("AT&T") and "O&#039;Neill" in wt.index_forms("O'Neill")
    got, _, err = wt.look_up(str(wt.index_path(wiki.dumps["en"])), ["AT&T"])
    assert err is None and got["AT&T"][0] == 52


def test_the_index_pass_gives_each_page_its_streams_bounds(wiki):
    """A page's stream ends where the next begins; the last runs to the end
    (0), as discovery writes a locator."""
    ix = str(wt.index_path(wiki.dumps["en"]))
    raw = bz2.decompress(Path(ix).read_bytes()).decode().splitlines()
    offsets = sorted({int(x.split(":", 1)[0]) for x in raw})
    got, lines, _ = wt.look_up(ix, [p[1] for p in RUN_PAGES])
    assert lines == len(RUN_PAGES) and len(got) == len(RUN_PAGES)
    for pid, start, end in got.values():
        assert end == (offsets[offsets.index(start) + 1] if start != offsets[-1] else 0)


def test_an_index_of_several_streams_is_read_whole(tmp_path, monkeypatch):
    """Concatenated bz2 streams are one file, as `bz2.open` reads them;
    trailing bytes that start no stream end it; a stream's end and a block's
    may fall anywhere."""
    import sys
    switch = sys.getswitchinterval()
    monkeypatch.setattr(wt, "RAW", 7)
    ix = tmp_path / "x-index.txt.bz2"
    ix.write_bytes(bz2.compress(b"0:1:One\n0:2:Two\n") + bz2.compress(b"500:3:Three\n900:4:Four") + b"\0\0")
    got, lines, err = wt.look_up(str(ix), ["Two", "Three", "Four"])
    assert err is None and lines == 4
    assert got == {"Two": (2, 0, 500), "Three": (3, 500, 900), "Four": (4, 900, 0)}
    assert b"".join(wt.blocks(ix)) == b"0:1:One\n0:2:Two\n500:3:Three\n900:4:Four"
    assert sys.getswitchinterval() == switch, "the pass's switch interval restored"


def test_the_index_pass_on_the_pool_finds_what_it_finds_here(wiki, monkeypatch):
    from app import config
    titles = ["Journey to the Centre of the Earth", "Bouvard and Pécuchet", "Nowhere at all", "Hop 2", "Nowhere yet"]
    here = resolve(wiki, titles)
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    assert resolve(wiki, titles) == here


def test_the_lookup_by_page_reads_by_the_primary_key(tmp_path):
    m = tmp_path / "m.db"
    sqlite3.connect(m).executescript(db.SCHEMA)
    con = sqlite3.connect(m)
    plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + wt.UID_SQL.format("?,?"), ("a", "b")))
    con.close()
    assert "USING INDEX sqlite_autoindex_documents_1 (uid=?)" in plan and "TEMP B-TREE" not in plan, plan


def test_the_runs_dumps_are_discoverys_own(wiki):
    """The run the collection holds, one dump a wiki; a second dump of one
    wiki in an unpinned corpus names none (which is the run's is not
    guessed)."""
    assert set(wt.run_dumps(wiki.corpus, RUN)) == {"en"} and set(wt.run_dumps(wiki.corpus, None)) == {"en"}
    other = wiki.corpus / "enwiki" / "20260901"
    other.mkdir()
    (other / f"enwiki-20260901{wsrc._DUMP_SUFFIX}").write_bytes(b"")
    assert set(wt.run_dumps(wiki.corpus, RUN)) == {"en"} and wt.run_dumps(wiki.corpus, None) == {}
    assert wt.run_dumps(None, RUN) == {} and native_id_of("en", 10, archive=True) == 10
