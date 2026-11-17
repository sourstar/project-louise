"""tests/hold/test_gutenberg_editions.py — `gutenberg-book-editions` re-derived
by the node itself every month (operator, 2026-11-17: "as if a monthly
Wikipedia snapshot were downloaded"; "if there is a local wikipedia collection
it is pointless to download a wikipedia snapshot"; app/gutenberg_editions.py,
the method `gutenberg-editions` in app/hold/annotation_methods.py): the
Wikidata tier resolved through the node's OWN wikipedia — titles normalised as
the API does, redirects followed, anthologies claiming nothing — and checked
against its wikidata; the same-title and stage tiers re-derived only where the
snapshot holds them; every other claim carried byte for byte; the same inputs
giving the same bytes; due once a period; a dry run that writes nothing; and
no network at all."""
import bz2
import json
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collection_file, db, gutenberg_titles, historical
from app import gutenberg_editions as ge
from app import wikipedia_titles as wt
from app.hold import annotation_methods as am
from app.hold import annotations as ann
from app.hold import collection as col
from app.hold import records as rec
from app.hold import shard as shard_mod
from app.hold import snapshot as sn
from app.hold.codex import License, Origin, Parser
from app.pack import records as pack_records
from app.plugins.base import DiscoverState
from app.plugins.wikipedia import SOURCE as WIKIPEDIA
from tests import wikipedia_props
from tests.hold.conftest import KEY, NOW, PLATFORM, PUBMED_SPEC, pubmed
from tests.test_historical import DANTE

NAME = "gutenberg-book-editions"
METHOD = "gutenberg-editions"
ANNOTATOR = "Louise reference node"
TARGET_SNAPSHOT = "2026-10"
NOVEMBER = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
LATE_NOVEMBER = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
DECEMBER = datetime(2026, 12, 2, 12, tzinfo=timezone.utc)
EN, ES, DE = "https://en.wikipedia.org/wiki/", "https://es.wikipedia.org/wiki/", "https://de.wikipedia.org/wiki/"


# ---- the node's gutenberg -------------------------------------------------------------

def a_book(n: int, lang: str, wiki=(), title: str | None = None, text: str = "a book", **extra):
    desc = "\n\n".join(f"Wikipedia page about this book: {u}" for u in wiki)
    ex = dict(extra, **({"description": desc} if desc else {}))
    sections = [["", text]]
    title = title or f"Book number {n}"
    return {"uid": f"gutenberg:{n}", "native_id": str(n), "lang": lang, "title": title, "creators": [],
            "date": None, "subjects": [], "keys": [], "sections": sections, "extra": ex,
            "origin": f"{n}/pg{n}.rdf", "content_hash": rec.content_hash(title, [tuple(s) for s in sections]),
            "origin_hash": "ab" * 16}


BOOKS = [
    a_book(7, "de", [EN + "King_Lear", DE + "Die_Ahnfrau"]),       # two items: an anthology
    a_book(50, "la", title="Aeneis"),
    a_book(51, "en", title="The Aeneid"),
    a_book(60, "it", title="Commedia", text=" ".join([DANTE] * 3)),
    a_book(70, "en", title="Sea Tales, Volume 1"),
    a_book(71, "en", title="Sea Tales, Volume 2"),
    a_book(996, "en", [EN + "Don_Quixote"], title="Don Quixote"),
    a_book(1532, "en", [EN + "King_Lear"], title="King Lear"),
    a_book(2000, "es", [ES + "Don_Quijote"], title="Don Quijote"),  # a wiki the node does not hold
    a_book(3000, "en", [EN + "old"], title="Old"),                  # two redirects, a lower-case first letter
    a_book(4000, "en", [ES + "Otro"], title="Otro"),                # not answerable, and no claim yet
    a_book(5903, "fi", [EN + "Quixote"], title="Don Quijote suomeksi"),   # one redirect
    a_book(8000, "en", [EN + "Orphan"], title="Orphan"),            # its page names no item now
]


def gutenberg_codex():
    return pubmed(plugin="gutenberg", license=License("green", None), parser=Parser("gutenberg", 9),
                  origin=Origin(spec=dict(PUBMED_SPEC, source_id="gutenberg"), form="the RDF and the text",
                                unit="container-member", clock="UTC", cutoff_rule="update-files",
                                jurisdiction=None))


def a_gutenberg(root: Path, books=BOOKS, sid=TARGET_SNAPSHOT):
    """The held target: one part of `books`, a snapshot `sid`; called again,
    the target's next snapshot, its part re-issued and the old one kept."""
    d = col.shard_dir_of(root, "gutenberg")
    first = not d.exists()
    target = col.create(root, gutenberg_codex(), name="gutenberg") if first else None
    books = sorted(books, key=lambda r: int(r["native_id"]))
    part = d / shard_mod.shard_path("all", 1)
    staged = d / "staged.jsonl.zst"
    pack_records.write_shard(staged, books)
    raw = staged.read_bytes()
    prev = None
    if first:
        part.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(part)
    else:
        from app.share import seeder
        prev = seeder.current_snapshot(d)
        shard_mod.promote(staged, part, shard_dir=d, new_root=shard_mod.pieces_root(raw))
    shard = sn.Shard(slice="all", n=1, root=shard_mod.pieces_root(raw),
                     records_root=rec.records_root(r["content_hash"] for r in books),
                     first_id=books[0]["native_id"], last_id=books[-1]["native_id"], records=len(books),
                     chunks=len(books), chunks_root="sha256:" + "22" * 32, tokens=512 * len(books), bytes=len(raw))
    now = {r["uid"]: r["content_hash"] for r in books}
    before = {} if prev is None else {r["uid"]: r["content_hash"] for r in BOOKS}
    snap = sn.Snapshot(collection_id=(target or prev).collection_id, snapshot_id=sid,
                       previous=None if prev is None else prev.hash, shards=(shard,),
                       delta=sn.compute_delta(before, now), files={})
    (d / "snapshots").mkdir(exist_ok=True)
    (d / shard_mod.snapshot_path(sid)).write_bytes(snap.canonical())
    return snap


# ---- the node's wikipedia and wikidata --------------------------------------------------

def _page(pid: int, title: str, text: str, redirect: str | None = None) -> str:
    red = f'<redirect title="{redirect}" />' if redirect else ""
    return (f"  <page>\n    <title>{title}</title>\n    <ns>0</ns>\n    <id>{pid}</id>\n    {red}\n"
            f"    <revision>\n      <id>{pid}00</id>\n      <timestamp>2026-10-01T00:00:00Z</timestamp>\n"
            f"      <text>{text}</text>\n    </revision>\n  </page>\n")


def a_wiki(corpus: Path, lang: str, pages, props) -> None:
    """`<lang>wiki/20261001/`: a multistream dump, one page a stream, its
    index and its run's page_props."""
    d = corpus / f"{lang}wiki" / "20261001"
    d.mkdir(parents=True)
    streams, lines, at = [], [], 0
    for pid, title, text, redirect in pages:
        s = bz2.compress(_page(pid, title, text, redirect).encode())
        lines.append(f"{at}:{pid}:{title}")
        streams.append(s)
        at += len(s)
    dump = d / f"{lang}wiki-20261001-pages-articles-multistream.xml.bz2"
    dump.write_bytes(b"".join(streams))
    (d / f"{lang}wiki-20261001-pages-articles-multistream-index.txt.bz2").write_bytes(
        bz2.compress(("\n".join(lines) + "\n").encode()))
    wikipedia_props.write(dump, props)


EN_PAGES = [(1, "Don Quixote", "Don Quixote is a novel.", None),
            (2, "Quixote", "#REDIRECT [[Don Quixote]]", "Don Quixote"),
            (3, "Old", "#REDIRECT [[Old two]]", "Old two"),
            (4, "Old two", "#REDIRECT [[Don Quixote]]", "Don Quixote"),
            (5, "King Lear", "King Lear is a tragedy.", None),
            (6, "Orphan", "Orphan is a word.", None)]
EN_PROPS = [(1, "wikibase_item", "Q480"), (5, "wikibase_item", "Q181598"), (6, "page_image_free", "x.jpg")]
DE_PAGES = [(1, "Die Ahnfrau", "Die Ahnfrau ist ein Drama.", None)]
DE_PROPS = [(1, "wikibase_item", "Q9")]


REDIRECTS = frozenset(t for _, t, _, red in EN_PAGES if red)


def a_manifest(path: Path, refs, *, untitled=()) -> Path:
    """A wikipedia manifest as the node's holds it: the rows discovery wrote
    and the titles the rdf stage wrote — a redirect indexed with no chunk;
    `untitled`: rows read by no rdf stage yet."""
    con = sqlite3.connect(path)
    con.executescript(db.SCHEMA)
    for r in refs:
        con.execute("INSERT INTO documents(uid, source, native_id, path, title, fts_done, chunks) "
                    "VALUES (?,?,?,?,?,1,?)", (f"wikipedia:{r.native_id}", "wikipedia", r.native_id, r.locator,
                                               r.extra["title"], 0 if r.extra["title"] in REDIRECTS else 2))
    for nid in untitled:
        con.execute("INSERT INTO documents(uid, source, native_id, path) VALUES (?,?,?,?)",
                    (f"wikipedia:{nid}", "wikipedia", str(nid), "x"))
    con.commit()
    con.close()
    return path


def a_wikidata(path: Path, items) -> Path:
    con = sqlite3.connect(path)
    con.executescript(db.SCHEMA)
    for q in items:
        con.execute("INSERT INTO documents(uid, source, native_id, path) VALUES (?,?,?,?)",
                    (f"wikidata:{q[1:]}", "wikidata", q[1:], f"dump#{q[1:]}"))
    con.commit()
    con.close()
    return path


def held_snapshot(root: Path, name: str, sid: str = TARGET_SNAPSHOT) -> None:
    """A snapshot id listed under `name`'s SHARD (all `against` reads)."""
    d = col.shard_dir_of(root, name) / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text("{}")


# ---- the claims the tools made ---------------------------------------------------------

def wd(subject, obj, q, s_url, o_url, s_lang, o_lang):
    return {"type": "same-work", "subject": subject, "object": obj, "subject_lang": s_lang, "object_lang": o_lang,
            "method": ge.WIKIDATA, "evidence": {"wikidata": q, "subject_wikipedia": s_url,
                                                "object_wikipedia": o_url},
            "confidence": ge.WIKIDATA_CONFIDENCE, "contributor": ge.CONTRIBUTOR}


CLASSICAL = {"type": "same-work", "subject": "gutenberg:50", "object": "gutenberg:51", "subject_lang": "la",
             "object_lang": "en", "method": {"kind": "matcher", "id": "louise-classical-matcher", "version": 1},
             "evidence": {"signals": ["title 0.81"], "tier": "title"}, "confidence": "0.95",
             "contributor": "Louise reference node (classical-translation measurement, 2026-10-09)"}
HUMAN = {"type": "language-stage", "subject": "gutenberg:60", "object": None, "method": {"kind": "human"},
         "evidence": {"why": "read by a person"}, "confidence": "1", "contributor": "a reader"}


def current_claims() -> list[bytes]:
    """The claims file as the tools' chain wrote it: the wikidata tier (one
    claim the node's wikipedia no longer makes), the classical matcher's
    compact line, the same-title tier, the stage tier and a person's claim."""
    t1 = [wd("gutenberg:2000", "gutenberg:996", "Q480", ES + "Don_Quijote", EN + "Don_Quixote", "es", "en"),
          wd("gutenberg:5903", "gutenberg:996", "Q480", EN + "Quixote", EN + "Don_Quixote", "fi", "en"),
          wd("gutenberg:8000", "gutenberg:1532", "Q181598", EN + "Orphan", EN + "King_Lear", "en", "en")]
    books = [ge.book_of(b) for b in BOOKS]
    t2 = gutenberg_titles.claims_of(books, [*t1, CLASSICAL])
    assert [(c["subject"], c["object"]) for c in t2] == [("gutenberg:71", "gutenberg:70")]
    stage = historical.stage_claim(next(b for b in BOOKS if b["uid"] == "gutenberg:60"), ge.STAGE_CONTRIBUTOR)
    assert stage is not None
    return ([ge._dumps(c) for c in t1]
            + [json.dumps(CLASSICAL, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()]
            + [ge._dumps(c) for c in t2] + [ge._canonical(stage), ge._dumps(HUMAN)])


@pytest.fixture
def node(tmp_path, monkeypatch):
    """The node: its gutenberg, wikipedia and wikidata, and the editions
    collection the tools made, its file recording no method yet."""
    root = tmp_path / "louise" / "collections"
    root.mkdir(parents=True)
    (tmp_path / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    a_gutenberg(root)
    corpus = tmp_path / "wiki-corpus"
    a_wiki(corpus, "en", EN_PAGES, EN_PROPS)
    a_wiki(corpus, "de", DE_PAGES, DE_PROPS)
    refs = [r for r in WIKIPEDIA.discover(DiscoverState(corpus_root=corpus)) if not r.error]
    assert len(refs) == len(EN_PAGES) + len(DE_PAGES)
    wiki = SimpleNamespace(name="wikipedia", plugin="wikipedia", corpus_dir=corpus,
                           manifest_path=a_manifest(tmp_path / "wikipedia.db", refs))
    items = SimpleNamespace(name="wikidata", plugin="wikidata",
                            manifest_path=a_wikidata(tmp_path / "wikidata.db", ["Q480", "Q181598", "Q9"]))
    held_snapshot(root, "wikipedia")
    held_snapshot(root, "wikidata", "2026-09")
    inputs = {"wikipedia": [wiki], "wikidata": [items]}
    monkeypatch.setattr(ge, "_held_of", lambda plugin: inputs.get(plugin, []))
    lines = current_claims()
    ann.create(root, NAME, target_ref="gutenberg", from_snapshot=TARGET_SNAPSHOT,
               claims=b"".join(line + b"\n" for line in lines), platform_image=PLATFORM,
               snapshot_id=TARGET_SNAPSHOT, key=KEY, now=NOW, nonce="0f" * 16, annotator=ANNOTATOR, log=lambda m: None)
    return SimpleNamespace(root=root, wiki=wiki, wikidata=items, inputs=inputs, lines=lines, tmp=tmp_path)


def derived(node, **kw):
    d = ge.derive(node.root, NAME, "gutenberg", log=lambda m: None, **kw)
    return d, {(t, c["subject"], c.get("object")): c for t, c, _ in d.lines}


# ---- the Wikidata tier, resolved locally ---------------------------------------------

def test_titles_are_normalised_as_the_api_normalises_them():
    assert wt.normalise("don_Quixote") == "Don Quixote"
    assert wt.normalise("  King__Lear  ") == "King Lear"
    assert wt.normalise("\u00dftra\u00dfe") == "\u00dftra\u00dfe", "a capital of two letters is not taken"
    assert wt.normalise("e" + "\u0301tude") == "\u00c9tude", "NFC, then the first letter"
    assert wt.normalise("A\u200eB\u00a0C") == "AB C"
    assert ge.host_lang("en.wikipedia.org") == "en" and ge.host_lang("wikipedia.org") == ""
    assert ge.split_wiki("https://en.m.wikipedia.org/wiki/Don_Quixote#Plot") == ("en.wikipedia.org", "Don Quixote")


def test_the_wikidata_tier_resolves_titles_redirects_and_anthologies_from_the_nodes_wikipedia(node):
    d, by = derived(node)
    t1 = {(s, o): c for (t, s, o), c in by.items() if t == ge.T_WIKIDATA}
    assert set(t1) == {("gutenberg:2000", "gutenberg:996"), ("gutenberg:3000", "gutenberg:996"),
                       ("gutenberg:5903", "gutenberg:996")}, \
        "Quixote → Don Quixote (a redirect), old → Old → Old two → Don Quixote (two), the es book kept"
    assert t1[("gutenberg:3000", "gutenberg:996")]["evidence"] == {
        "wikidata": "Q480", "subject_wikipedia": EN + "old", "object_wikipedia": EN + "Don_Quixote"}
    assert ("gutenberg:7", "gutenberg:1532") not in t1, "King Lear and Die Ahnfrau: two items, no claim"
    r = d.resolution
    assert r["redirects_followed"] == 2 and r["by_reason"]["unknown: wiki not held"] == 2
    assert r["by_reason"]["none: no item"] == 1 and r["wikis_not_held"] == {"es": 2}
    assert r["books"]["undetermined: a URL the node cannot answer, and no item in the current claims"] == 1
    assert r["books"]["their item kept through a URL the node cannot answer"] == 1
    assert d.inputs == {"gutenberg": TARGET_SNAPSHOT, "wikipedia": TARGET_SNAPSHOT, "wikidata": "2026-09"}


def test_the_tier_is_the_tools_rule_byte_for_byte():
    """What the node answers for a title stands where the API's answer
    stood: the same books, the same answers, the same claims."""
    from tools import annotations_from_works as tool
    books = [ge.book_of(b) for b in BOOKS if all(u.startswith(EN) for u in ge.book_of(b).get("wiki", [""]))]
    api = {"titles": {"en.wikipedia.org|Don Quixote": "Q480", "en.wikipedia.org|Quixote": "Q480",
                      "en.wikipedia.org|old": "Q480", "en.wikipedia.org|King Lear": "Q181598"}, "items": {}}
    answers = {("en.wikipedia.org", t): q for k, q in api["titles"].items() for t in [k.split("|")[1]]}

    def local(book):
        found = {}
        for url in book.get("wiki") or []:
            q = answers.get(ge.split_wiki(url))
            if q:
                found.setdefault(q, url)
        return found
    got = ge.wikidata_claims(books, local)
    assert got == tool.claims_of(books, api) and len(got) == 2
    assert b"".join(ge._dumps(c) for c in got) == b"".join(
        json.dumps(c, ensure_ascii=False, sort_keys=True).encode() for c in tool.claims_of(books, api))


def test_an_item_the_nodes_wikidata_lacks_is_not_taken_for_none(node):
    """page_props can name an item merged since: the claims that named it
    keep it through their own URL; a book new to it claims nothing."""
    node.inputs["wikidata"] = [SimpleNamespace(name="wikidata", plugin="wikidata", manifest_path=a_wikidata(
        node.tmp / "wikidata-less.db", ["Q181598", "Q9"]))]
    d, by = derived(node)
    t1 = {(s, o) for (t, s, o) in by if t == ge.T_WIKIDATA}
    assert t1 == {("gutenberg:2000", "gutenberg:996"), ("gutenberg:5903", "gutenberg:996")}, \
        "kept as they stand; 3000 (not claimed yet) claims nothing"
    assert d.resolution["by_reason"]["unknown: not in wikidata"] == 3


def test_a_title_in_a_language_not_read_yet_is_unknown_not_none(tmp_path):
    """"No page" only on proof (2026-11-17): a title the manifest misses is
    looked up in the run's own index — held by the run and not read here,
    UNKNOWN; listed nowhere, NONE; no index to ask, UNKNOWN. Untitled rows no
    longer decide it: the index does (tests/test_wikipedia_titles.py)."""
    from app.plugins.wikipedia import ids
    corpus = tmp_path / "c"
    a_wiki(corpus, "en", EN_PAGES + [(7, "Nowhere yet", "Nowhere yet is a page.", None)], EN_PROPS)
    refs = [r for r in WIKIPEDIA.discover(DiscoverState(corpus_root=corpus)) if not r.error]
    m = a_manifest(tmp_path / "m.db", [r for r in refs if r.extra["title"] != "Nowhere yet"],
                   untitled=[ids.native_id_of("en", 99)])
    dumps = wt.run_dumps(corpus, None)
    got = wt.resolve(m, [("en", "Nowhere_yet"), ("en", "Nowhere"), ("en", "Don_Quixote"), ("fr", "Nulle part")],
                     dumps=dumps, log=lambda s: None)
    assert got[("en", "Nowhere_yet")].kind == wt.UNKNOWN and got[("en", "Nowhere_yet")].code == wt.NOT_READ
    assert (got[("en", "Nowhere")].kind, got[("en", "Nowhere")].code) == (wt.NONE, wt.NO_PAGE)
    assert got[("en", "Nowhere")].run == "20261001", "the run whose index proves it"
    assert got[("en", "Don_Quixote")].item == "Q480"
    assert got[("fr", "Nulle part")].code == wt.NOT_HELD
    m2 = a_manifest(tmp_path / "m2.db", refs)
    assert wt.resolve(m2, [("en", "Nowhere")], log=lambda s: None)[("en", "Nowhere")].code == wt.NO_DUMP, \
        "no index to ask: never 'no page'"
    none_held = wt.resolve(tmp_path / "absent.db", [("fi", "Muu"), ("", "x")], log=lambda s: None)
    assert {r.code for r in none_held.values()} == {wt.NOT_HELD}, "no pass, no manifest opened"


def test_the_pass_reads_by_rowid_windows_and_lookups_by_key(tmp_path):
    """One pass by the primary key, never a sort; the candidates and the
    items by their unique indexes (CLAUDE.md's selection-query rules)."""
    m = tmp_path / "m.db"
    sqlite3.connect(m).executescript(db.SCHEMA)
    con = sqlite3.connect(m)
    plans = [" ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)) for sql, args in (
        (wt.SCAN_SQL, (0, 10, 5)), (wt.ROW_SQL.format("?,?"), (1, 2)),
        ("SELECT native_id FROM documents WHERE source = ? AND native_id IN (?,?)", ("wikidata", "1", "2")))]
    con.close()
    assert all("USING INTEGER PRIMARY KEY" in p for p in plans[:2]), plans
    assert "INDEX idx_documents_native" in plans[2], plans
    assert not any("TEMP B-TREE" in p or "LIST SUBQUERY" in p or p.startswith("SCAN") for p in plans), plans


def test_the_pass_on_the_pool_finds_what_it_finds_here(node, monkeypatch):
    from app import config
    here = wt.index(node.wiki.manifest_path, log=lambda m: None)
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(wt, "TASK_ROWS", 2)
    pooled = wt.index(node.wiki.manifest_path, log=lambda m: None)
    assert pooled.words.tolist() == here.words.tolist() and pooled.titled == here.titled


# ---- "no page" only on proof; evidence older than the claim (the node's dry run, 2026-11-17) --------

#: the books of the node's withdrawn stars, each star's centre its lowest number
STARS = [a_book(3748, "en", [EN + "Journey_to_the_Center_of_the_Earth"], title="Voyage"),
         a_book(4791, "en", [EN + "Journey_to_the_Centre_of_the_Earth"], title="Journey I"),
         a_book(8983, "fr", [EN + "Journey_to_the_Centre_of_the_Earth"], title="Voyage II"),
         a_book(775, "en", [EN + "The_Sleeper_Awakes"], title="Sleeper"),
         a_book(12163, "en", [EN + "When_the_Sleeper_Wakes"], title="Sleeper I"),
         a_book(51529, "de", [EN + "When_the_Sleeper_Wakes"], title="Sleeper II"),
         a_book(14157, "en", [EN + "Bouvard_et_P%C3%A9cuchet"], title="Bouvard"),
         a_book(25014, "en", [EN + "Bouvard_and_P%C3%A9cuchet"], title="Bouvard I"),
         a_book(27479, "en", [EN + "Encyclop%C3%A6dia_Britannica_Eleventh_Edition"], title="EB"),
         a_book(30685, "en", [EN + "Eleventh_edition_of_the_Encyclop%C3%A6dia_Britannica"], title="EB I"),
         a_book(90000, "en", [EN + "Nowhere_at_all"], title="Nowhere")]
STAR_CLAIMS = [("gutenberg:4791", "gutenberg:3748", "Q3", "Journey_to_the_Centre_of_the_Earth",
                "Journey_to_the_Center_of_the_Earth", "en", "en"),
               ("gutenberg:8983", "gutenberg:3748", "Q3", "Journey_to_the_Centre_of_the_Earth",
                "Journey_to_the_Center_of_the_Earth", "fr", "en"),
               ("gutenberg:12163", "gutenberg:775", "Q4", "When_the_Sleeper_Wakes", "The_Sleeper_Awakes", "en", "en"),
               ("gutenberg:51529", "gutenberg:775", "Q4", "When_the_Sleeper_Wakes", "The_Sleeper_Awakes", "de", "en"),
               ("gutenberg:25014", "gutenberg:14157", "Q1", "Bouvard_and_P%C3%A9cuchet", "Bouvard_et_P%C3%A9cuchet",
                "en", "en"),
               ("gutenberg:30685", "gutenberg:27479", "Q2", "Eleventh_edition_of_the_Encyclop%C3%A6dia_Britannica",
                "Encyclop%C3%A6dia_Britannica_Eleventh_Edition", "en", "en")]
#: the claims were made (their snapshot signed) before the node's run, 20260801:
#: a "no page" from that run would be evidence against them
BEFORE_THE_RUN = "2026-07-01T00:00:00Z"


@pytest.fixture
def stars(tmp_path, monkeypatch):
    """The node's pattern: the wikipedia of tests/test_wikipedia_titles.py
    (layer 1's 2024 titles, the run's redirects without manifest rows), and
    the stars the tools made through the live API."""
    from tests import test_wikipedia_titles as twt
    root = tmp_path / "louise" / "collections"
    root.mkdir(parents=True)
    (tmp_path / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    a_gutenberg(root, STARS)
    corpus = tmp_path / "wikipedia"
    twt.an_archive(corpus)
    twt.a_run(corpus)
    refs = [r for r in WIKIPEDIA.discover(DiscoverState(corpus_root=corpus, snapshot=twt.RUN)) if r.scope == "document"]
    wiki = SimpleNamespace(name="wikipedia", plugin="wikipedia", corpus_dir=corpus, snapshot=twt.RUN,
                           manifest_path=twt.a_manifest(tmp_path / "wikipedia.db", refs))
    items = SimpleNamespace(name="wikidata", plugin="wikidata",
                            manifest_path=a_wikidata(tmp_path / "wikidata.db", [f"Q{i}" for i in range(1, 9)]))
    held_snapshot(root, "wikipedia")
    held_snapshot(root, "wikidata", "2026-09")
    monkeypatch.setattr(ge, "_held_of", lambda plugin: {"wikipedia": [wiki], "wikidata": [items]}.get(plugin, []))
    lines = [ge._dumps(wd(s, o, q, EN + su, EN + ou, sl, ol)) for s, o, q, su, ou, sl, ol in STAR_CLAIMS]
    ann.create(root, NAME, target_ref="gutenberg", from_snapshot=TARGET_SNAPSHOT,
               claims=b"".join(line + b"\n" for line in lines), platform_image=PLATFORM, snapshot_id=TARGET_SNAPSHOT,
               key=KEY, now=BEFORE_THE_RUN, nonce="0d" * 16, annotator=ANNOTATOR, log=lambda m: None)
    return SimpleNamespace(root=root, wiki=wiki, lines=lines, corpus=corpus, run=twt.RUN)


def test_the_journey_sleeper_and_moved_titles_keep_their_claims(stars):
    """The eight Journey books and the two Sleeper books through enwiki
    redirects the manifest holds no row for; Bouvard and the Britannica
    through titles layer 1 holds under the archive's 2024 ones: every claim
    kept as it stands, none withdrawn, none re-centred."""
    d = ge.derive(stars.root, NAME, "gutenberg", log=lambda m: None)
    shown = ge.report(d)
    t = shown["tiers"][ge.T_WIKIDATA]
    assert (t["kept_identical"], t["withdrawn"], t["added"], t["changed"]) == (6, 0, 0, 0), t
    assert sorted(d.bytes().splitlines()) == sorted(stars.lines), "byte for byte (written in the items' order)"
    r = d.resolution
    assert r["runs"] == [stars.run] and r["redirects_followed"] == 4 and r["found_by_page_id"] == 4, \
        "Journey, Sleeper, and the two old titles: redirects of the run; the four Bouvard and Britannica titles " \
        "reach pages layer 1 holds under other titles"
    assert r["by_reason"] == {"none: no page": 1}
    assert r["no_page_titles"] == [{"wiki": "en", "title": "Nowhere at all", "books": ["gutenberg:90000"]}], \
        "every 'no page' listed, title by title, with its books"
    assert {(x["title"], x["manifest_title"]) for x in r["by_page_id_titles"]} == {
        ("Bouvard and Pécuchet", "Bouvard et Pécuchet"), ("Bouvard et Pécuchet", "Bouvard et Pécuchet"),
        ("Eleventh edition of the Encyclopædia Britannica", "Encyclopædia Britannica Eleventh Edition"),
        ("Encyclopædia Britannica Eleventh Edition", "Encyclopædia Britannica Eleventh Edition")}
    assert not r["books"].get(ge.OLDER_NONE) and not r["books"].get(ge.OLDER_ITEM), \
        "the run is newer than the claims: each kept on its own answer"


def test_without_the_runs_dump_the_claims_are_kept_and_nothing_is_no_page(stars):
    wt.index_path(wt.run_dumps(stars.corpus, stars.run)["en"]).unlink()
    d = ge.derive(stars.root, NAME, "gutenberg", log=lambda m: None)
    t = ge.report(d)["tiers"][ge.T_WIKIDATA]
    assert (t["kept_identical"], t["withdrawn"]) == (6, 0)
    assert "none: no page" not in d.resolution["by_reason"] and d.resolution["by_reason"]["unknown: no dump to tell"] == 5


def test_a_none_from_a_run_older_than_the_claim_keeps_it(node):
    """Orphan names no item in the node's run (20261001). Claims signed
    2026-09-08: the run is newer, the claim withdrawn (the dry run's test).
    The same claims signed 2026-10-15: the run predates them, its "no item"
    is no evidence, the claim kept."""
    late = "2026-10-15T00:00:00Z"
    ann.create(node.root, "late-editions", target_ref="gutenberg", from_snapshot=TARGET_SNAPSHOT,
               claims=b"".join(line + b"\n" for line in node.lines), platform_image=PLATFORM,
               snapshot_id=TARGET_SNAPSHOT, key=KEY, now=late, nonce="0c" * 16, annotator=ANNOTATOR, log=lambda m: None)
    _, held = ge.held_claims(node.root, "late-editions")
    assert set(ge.claim_times(node.root, "late-editions", [h.uid for h in held]).values()) == {late}
    _, by = derived(node)
    assert (ge.T_WIKIDATA, "gutenberg:8000", "gutenberg:1532") not in by, "newer run: withdrawn"
    late_d = ge.derive(node.root, "late-editions", "gutenberg", log=lambda m: None)
    kept = {(c["subject"], c["object"]) for t, c, _ in late_d.lines if t == ge.T_WIKIDATA}
    assert ("gutenberg:8000", "gutenberg:1532") in kept
    assert late_d.resolution["books"][ge.OLDER_NONE] == 1
    assert ge.report(late_d)["tiers"][ge.T_WIKIDATA]["withdrawn"] == 0


def test_an_older_runs_other_item_keeps_the_newer_claim(stars):
    """An older run never overrides a newer claim, in either direction: the
    run names Q1 for both Bouvard links, a claim made after it says Q77 —
    kept, Q77, through its URLs. The same claim made before the run: the run
    is the evidence, the claim re-made with Q1."""
    line = ge._dumps(wd("gutenberg:25014", "gutenberg:14157", "Q77", EN + "Bouvard_and_P%C3%A9cuchet",
                        EN + "Bouvard_et_P%C3%A9cuchet", "en", "en"))
    for name, when, nonce in (("after-the-run", "2026-10-15T00:00:00Z", "0b"), ("before-the-run", BEFORE_THE_RUN, "0a")):
        ann.create(stars.root, name, target_ref="gutenberg", from_snapshot=TARGET_SNAPSHOT, claims=line + b"\n",
                   platform_image=PLATFORM, snapshot_id=TARGET_SNAPSHOT, key=KEY, now=when, nonce=nonce * 16,
                   annotator=ANNOTATOR, log=lambda m: None)
    after = ge.derive(stars.root, "after-the-run", "gutenberg", log=lambda m: None)
    items = {(c["subject"], c["object"]): c["evidence"]["wikidata"] for t, c, _ in after.lines if t == ge.T_WIKIDATA}
    assert items[("gutenberg:25014", "gutenberg:14157")] == "Q77" and line in after.bytes().splitlines()
    assert after.resolution["books"][ge.OLDER_ITEM] == 2 and not after.resolution["books"].get(ge.OLDER_NONE)
    assert ge.report(after)["tiers"][ge.T_WIKIDATA]["withdrawn"] == 0
    before = ge.derive(stars.root, "before-the-run", "gutenberg", log=lambda m: None)
    items = {(c["subject"], c["object"]): c["evidence"]["wikidata"] for t, c, _ in before.lines if t == ge.T_WIKIDATA}
    assert items[("gutenberg:25014", "gutenberg:14157")] == "Q1" and not before.resolution["books"].get(ge.OLDER_ITEM)


def test_a_claim_is_dated_by_the_first_snapshot_that_holds_it(node):
    """Kept through an update, a claim keeps its first snapshot's time; one
    the update adds takes the update's."""
    collection_file.set_values(NAME, {"method": METHOD})
    am.derive(node.root, NAME, METHOD, key=KEY, now="2026-11-17T12:00:00Z", clock=NOVEMBER, log=lambda m: None)
    _, held = ge.held_claims(node.root, NAME)
    times = ge.claim_times(node.root, NAME, [h.uid for h in held])
    by_subject = {h.claim["subject"]: times[h.uid] for h in held if h.tier == ge.T_WIKIDATA}
    assert by_subject == {"gutenberg:2000": NOW, "gutenberg:5903": NOW, "gutenberg:3000": "2026-11-17T12:00:00Z"}
    assert ge.made_after([NOW], "20260908") and not ge.made_after([NOW], "20260909")
    assert ge.made_after([None], "20991231") and ge.made_after([NOW], None), "unknown: no evidence"


def test_a_star_keeps_its_centre_while_its_membership_does():
    """The centre is the lowest ebook number among the books naming the item
    and no other: a function of the membership alone, in any order. A member
    that leaves takes its own claim only; the centre leaving re-centres the
    star, and every claim of it is new — what 83 withdrawn against 19 added
    on the node was."""
    books = [{"uid": f"gutenberg:{n}", "lang": "en", "wiki": [EN + "X"]} for n in (3748, 4791, 8983)]

    def one(b):
        return {"Q3": b["wiki"][0]}
    a = ge.wikidata_claims(books, one)
    assert ge.wikidata_claims(list(reversed(books)), one) == a and {c["object"] for c in a} == {"gutenberg:3748"}
    assert ge.wikidata_claims(books[:2], one) == a[:1], "a member leaves: the others' claims stand"
    assert [(c["subject"], c["object"]) for c in ge.wikidata_claims(books[1:], one)] == [
        ("gutenberg:8983", "gutenberg:4791")], "the centre leaves: re-centred on the lowest left"


# ---- the other tiers, and what is carried -------------------------------------------

def test_carried_and_unchanged_claims_are_written_as_their_lines_byte_for_byte(node):
    d, _ = derived(node)
    out = d.bytes().split(b"\n")
    for line in node.lines:
        c = json.loads(line)
        if c["subject"] in ("gutenberg:8000",):
            assert line not in out, "withdrawn: the node's wikipedia names no item for Orphan"
        else:
            assert line in out, f"kept byte for byte: {c['subject']} {c['type']}"
    tiers = [t for t, _, _ in d.lines]
    assert tiers == [ge.T_WIKIDATA] * 3 + [ge.T_CARRIED] * 2 + [ge.T_TITLES, ge.T_STAGE], \
        "wikidata, carried in their lines' order, same-title, stage"
    assert [c["method"]["kind"] for t, c, _ in d.lines if t == ge.T_CARRIED] == ["matcher", "human"]


def test_the_same_inputs_give_the_same_bytes(node):
    a = ge.derive(node.root, NAME, "gutenberg", log=lambda m: None).bytes()
    b = ge.derive(node.root, NAME, "gutenberg", log=lambda m: None).bytes()
    assert a == b and a.count(b"\n") == 7


def test_the_title_and_stage_tiers_are_made_only_where_the_snapshot_holds_them(node):
    root = node.tmp / "bare"
    root.mkdir()
    a_gutenberg(root)
    held_snapshot(root, "wikipedia")
    held_snapshot(root, "wikidata", "2026-09")
    only = [line for line in node.lines if json.loads(line)["method"]["kind"] != "matcher"
            or json.loads(line)["method"]["id"] == "louise-classical-matcher"]
    ann.create(root, "bare-editions", target_ref="gutenberg", from_snapshot=TARGET_SNAPSHOT,
               claims=b"".join(line + b"\n" for line in only), platform_image=PLATFORM,
               snapshot_id=TARGET_SNAPSHOT, key=KEY, now=NOW, nonce="0e" * 16, annotator=ANNOTATOR, log=lambda m: None)
    d = ge.derive(root, "bare-editions", "gutenberg", log=lambda m: None)
    assert d.active == {ge.T_WIKIDATA: True, ge.T_TITLES: False, ge.T_STAGE: False}
    assert {t for t, _, _ in d.lines} == {ge.T_WIKIDATA, ge.T_CARRIED}, "no same-title or stage claim made"
    shown = ge.report(d)["tiers"]
    assert shown[ge.T_TITLES]["re_derived"] is False and "holds no claim" in shown[ge.T_TITLES]["why"]


# ---- the method: monthly, recorded, a dry run --------------------------------------

def test_the_method_fits_the_collection_and_says_what_it_does(node):
    m = am.METHODS[METHOD]
    assert m.label == "update by itself every month from the node's Gutenberg, Wikipedia and Wikidata"
    assert m.cadence == "monthly" and m.preview is not None and m.inputs == ("wikipedia", "wikidata")
    assert am.not_fitting(node.root, NAME, m) is None
    assert am.not_fitting(node.root, NAME, am.METHODS["wikipedia-versions"]) is not None
    collection_file.set_values(NAME, {"method": METHOD})
    from app import collections as collections_mod
    assert collections_mod.load_annotations()[0].method == METHOD


def test_a_collection_of_carried_claims_alone_does_not_fit(node):
    """Ticking the box on a collection the classical matcher made alone would
    add a Wikidata tier to it: refused (2026-11-17 review)."""
    root = node.root
    only = [line for line in node.lines if json.loads(line)["method"].get("id") == "louise-classical-matcher"]
    assert only, "the fixture holds a classical claim"
    ann.create(root, "classical-only", target_ref="gutenberg", from_snapshot=TARGET_SNAPSHOT,
               claims=b"".join(line + b"\n" for line in only), platform_image=PLATFORM,
               snapshot_id=TARGET_SNAPSHOT, key=KEY, now=NOW, nonce="0f" * 16, annotator=ANNOTATOR,
               log=lambda m: None)
    why = am.not_fitting(root, "classical-only", am.METHODS[METHOD])
    assert why is not None and "would only carry" in why


def test_due_once_a_period_and_not_when_the_period_is_not_open(node):
    collection_file.set_values(NAME, {"method": METHOD})
    d = am.due(node.root, NAME, METHOD, clock=NOVEMBER)
    assert d.due and d.period == "2026-11", d.reason
    early = am.due(node.root, NAME, METHOD, clock=datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert not early.due and early.waiting and "once its period opens" in early.reason, \
        "2026-10 is October's own snapshot: the next is written in November"
    got = am.derive(node.root, NAME, METHOD, key=KEY, now=NOW, clock=NOVEMBER, log=lambda m: None)
    assert got["snapshot_id"] == "2026-11" and got["added"] == 1 and got["tombstoned"] == 1
    mk = am.read_marker(node.root, NAME)
    assert mk["against"] == {"gutenberg": TARGET_SNAPSHOT, "wikipedia": TARGET_SNAPSHOT, "wikidata": "2026-09"}
    assert (mk["method"], mk["snapshot"], mk["period"], mk["written"]) == (METHOD, "2026-11", "2026-11", True)
    again = am.due(node.root, NAME, METHOD, clock=LATE_NOVEMBER)
    assert not again.due and not again.waiting, "not twice in one period"
    assert am.due(node.root, NAME, METHOD, clock=DECEMBER).due, "a new period: due, whatever gutenberg holds"


def test_a_run_with_nothing_to_write_is_not_repeated_in_its_period(node):
    collection_file.set_values(NAME, {"method": METHOD})
    am.derive(node.root, NAME, METHOD, key=KEY, now=NOW, clock=NOVEMBER, log=lambda m: None)
    got = am.derive(node.root, NAME, METHOD, key=KEY, now=NOW, clock=DECEMBER, log=lambda m: None)
    assert got["written"] is False, "the same inputs: nothing to update"
    mk = am.read_marker(node.root, NAME)
    assert (mk["period"], mk["written"]) == ("2026-12", False)
    assert not am.due(node.root, NAME, METHOD, clock=DECEMBER).due


def test_the_worker_derives_it_once_in_a_period(node):
    collection_file.set_values(NAME, {"method": METHOD})
    lines: list[str] = []

    def run(root_, name, method, stop=None):
        return am.derive(root_, name, method, key=KEY, now=NOW, log=lines.append, clock=NOVEMBER)
    (got,) = am.maintain(node.root, log=lines.append, clock=NOVEMBER, run=run)
    assert got["snapshot_id"] == "2026-11"
    assert any("derived with gutenberg-editions in 2026-11, against gutenberg 2026-10, wikidata 2026-09, "
               "wikipedia 2026-10" in m for m in lines), lines
    assert am.maintain(node.root, log=lines.append, clock=LATE_NOVEMBER, run=run) == []


def test_a_dry_run_reports_each_tier_and_writes_nothing(node, monkeypatch, capsys):
    from app import louise
    collection_file.set_values(NAME, {"method": METHOD})
    clock = type("D", (), {"now": staticmethod(lambda tz=None: NOVEMBER)})
    monkeypatch.setattr(ann, "datetime", clock)
    monkeypatch.setattr(am, "datetime", clock)
    tree = node.root / NAME

    def files():
        return {p.relative_to(tree).as_posix(): p.read_bytes() for p in sorted(tree.rglob("*")) if p.is_file()}
    before = files()
    assert louise.main(["hold", "annotations", "derive", NAME, "--root", str(node.root), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["due"] is True and files() == before, "nothing written: no snapshot, no claims, no marker"
    tiers = out["preview"]["tiers"]
    assert {k: tiers[ge.T_WIKIDATA][k] for k in ("re_derived", "kept_identical", "changed", "added",
                                                  "withdrawn", "set_aside")} == {
        "re_derived": 3, "kept_identical": 2, "changed": 0, "added": 1, "withdrawn": 1, "set_aside": 0}
    ex = tiers[ge.T_WIKIDATA]["examples"]
    assert [c["subject"] for c in ex["added"]] == ["gutenberg:3000"]
    assert [c["subject"] for c in ex["withdrawn"]] == ["gutenberg:8000"]
    assert tiers[ge.T_CARRIED] == {"carried": 2, "set_aside": 0}
    assert tiers[ge.T_TITLES]["kept_identical"] == 1 and tiers[ge.T_STAGE]["kept_identical"] == 1
    assert out["preview"]["against"]["wikipedia"] == TARGET_SNAPSHOT


def test_a_carried_claim_about_a_record_the_target_withdrew_is_set_aside(node):
    """The target's next snapshot drops the classical pair's Latin book: the
    carried claim is written as it stands, and `update` sets it aside."""
    a_gutenberg(node.root, [b for b in BOOKS if b["uid"] != "gutenberg:50"], sid="2026-11")
    shown = ge.report(ge.derive(node.root, NAME, "gutenberg", log=lambda m: None))["tiers"]
    assert shown[ge.T_CARRIED] == {"carried": 2, "set_aside": 1}
    collection_file.set_values(NAME, {"method": METHOD})
    got = am.derive(node.root, NAME, METHOD, key=KEY, now=NOW, clock=NOVEMBER, log=lambda m: None)
    assert got["dropped_count"] == 1 and "gutenberg:50" in got["dropped"][0]


def test_no_network_is_touched(node, monkeypatch):
    def refused(*a, **k):
        raise AssertionError("the method reached for the network")
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)
    import httpx
    monkeypatch.setattr(httpx.Client, "send", refused)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", refused)
    collection_file.set_values(NAME, {"method": METHOD})
    got = am.derive(node.root, NAME, METHOD, key=KEY, now=NOW, clock=NOVEMBER, log=lambda m: None)
    assert got["snapshot_id"] == "2026-11"


# ---- from the delta (docs/notes/collection-updates.md §4) --------------------------------

def test_the_part_cache_reads_only_a_part_gutenberg_re_issued_and_gives_the_same_bytes(node, monkeypatch):
    """A part read once a ROOT: the second run reads no part and writes the
    first run's bytes; after gutenberg re-issues its part, that part alone is
    read, and the bytes are a run's with no cache at all."""
    reads: list[str] = []
    real = ge._read_part
    monkeypatch.setattr(ge, "_read_part", lambda path: reads.append(path) or real(path))
    first = ge.derive(node.root, NAME, "gutenberg", log=lambda m: None).bytes()
    assert len(reads) == 1
    said: list[str] = []
    assert ge.derive(node.root, NAME, "gutenberg", log=said.append).bytes() == first and len(reads) == 1
    assert any("0 read, 1 unchanged, from the part cache" in m for m in said)
    a_gutenberg(node.root, BOOKS + [a_book(9000, "en", [EN + "King_Lear"], title="King Lear, again")], sid="2026-11")
    cached = ge.derive(node.root, NAME, "gutenberg", log=lambda m: None)
    assert len(reads) == 2, "the re-issued part, read"
    store = Path(ge.cache_store(col.shard_dir_of(node.root, "gutenberg")))
    assert len(list(store.glob("v*.json"))) == 1, "the superseded part's file removed"
    for p in store.glob("v*.json"):
        p.unlink()
    cold = ge.derive(node.root, NAME, "gutenberg", log=lambda m: None)
    assert cached.bytes() == cold.bytes() and cached.bytes() != first
    # a dry run reads the cache and writes none
    for p in store.glob("v*.json"):
        p.unlink()
    ge.preview(node.root, NAME, "gutenberg", log=lambda m: None)
    assert not list(store.glob("v*.json"))
    # a file of another code version is another name: never read as this one's
    monkeypatch.setattr(ge, "books_version", lambda: 7)
    ge.derive(node.root, NAME, "gutenberg", log=lambda m: None)
    assert [p.name.split("-")[0] for p in store.glob("v*.json")] == ["v7"]


def test_derive_from_delta_writes_the_whole_derivations_bytes(node, monkeypatch, tmp_path):
    """The cascade's step after gutenberg's next snapshot: the method whole
    (its Wikidata tier reads what no delta names), said; its update re-pinned
    from the delta; every byte the whole derivation's."""
    import shutil

    from app import parallel
    from app.hold import annotations_delta as ad
    from tests.hold.test_annotations_stream import tree
    monkeypatch.setattr(ad, "MAX_FRAMES", 1.0)          # 14 books in one frame: served whatever the estimate
    collection_file.set_values(NAME, {"method": METHOD})
    changed = [dict(b, sections=[["", "a book, revised"]], content_hash=rec.content_hash(b["title"], [("", "a book, revised")]))
               if b["uid"] == "gutenberg:71" else b for b in BOOKS]
    a_gutenberg(node.root, changed, sid="2026-11")
    twin = tmp_path / "twin" / "collections"
    shutil.copytree(node.root, twin, symlinks=True)
    with __import__("app.hold.annotations_stream", fromlist=["x"]).workspace(node.root, "reference") as work:
        am.produce(node.root, NAME, METHOD, work / "c.jsonl", log=lambda m: None)
        whole = am.finish(node.root, NAME, METHOD, work / "c.jsonl", key=KEY, now=NOW, clock=NOVEMBER,
                          log=lambda m: None, since=None)
    monkeypatch.setattr(parallel, "watch_parent", lambda: None)
    monkeypatch.setattr(am, "_stop_outside_last_writes", lambda: None)
    monkeypatch.setattr(am, "_node_key", lambda name: KEY)
    real_finish = am.finish
    monkeypatch.setattr(am, "finish", lambda *a, **k: real_finish(*a, **{**k, "now": NOW, "clock": NOVEMBER}))

    def run(target_fn, args, *, stop, poll_s, label, error=None):
        with pytest.raises(SystemExit) as e:
            target_fn(*args)
        if e.value.code:
            raise am.ChildFailed(e.value.code, error.read_text())
    monkeypatch.setattr(am, "_run", run)
    got = am.derive_from_delta(twin, NAME, METHOD, {"gutenberg": (TARGET_SNAPSHOT, "2026-11")}, log=lambda m: None)
    assert tree(twin / NAME) == tree(node.root / NAME), "the same bytes, every file"
    assert {k: v for k, v in got.items() if k not in ("form", "delta", "produced", "method")} == \
        {k: v for k, v in whole.items() if k != "delta"}
    assert got["form"]["method"] == "whole" and "page_props" in got["form"]["method_why"]
    u = got["form"]["update"]
    assert u["targets"]["gutenberg"]["from"] == TARGET_SNAPSHOT and u["pinned"] > 0


# ---- the book, as the measurement read it ---------------------------------------------

def test_a_book_is_read_as_the_measurement_wrote_it(monkeypatch, capsys):
    """`book_of` is tools/measure/one_language_records.py's projection, field
    for field: the claims the method keeps were made from that file."""
    import runpy

    from app.pack import records as pack_records_mod
    from app.plugins import shard as shard_plugin
    long = a_book(12, "en", [EN + "Hamlet).", EN + "Ham(let)"], title="T" * 300,
                  creators_detail=[{"role": "aut", "agent_id": "2009/agents/1", "name": "A"},
                                   {"role": "trl", "agent_id": "", "name": "B"}],
                  marc={"904": [" https://archive.org/x "], "010": ["  12345 "]})
    long["creators"] = [f"C{i}" for i in range(9)]
    bare = a_book(13, "fr", creators_detail="[]", marc="{'904': ['s']}")
    monkeypatch.setattr(shard_plugin, "shard_files", lambda d: [Path("x")])
    monkeypatch.setattr(pack_records_mod, "iter_records", lambda p: iter([long, bare]))
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "tools" / "measure" / "one_language_records.py"))
    measured = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    keys = ("uid", "lang", "title", "creators", "wiki", "agents", "scan", "lccn")
    for m, r in zip(measured, (long, bare)):
        mine = ge.book_of(r)
        assert {k: m[k] for k in keys if k in m} == mine
    assert ge.book_of(long)["wiki"] == [EN + "Hamlet", EN + "Ham(let)"] and len(ge.book_of(long)["title"]) == 200


def test_a_withdrawn_claim_says_whether_its_books_are_still_one_work():
    """München's dry run, 2026-11-17: 11 same-title and 3 Wikidata claims
    withdrawn, and whether each pair was lost or is joined another way could
    not be read off the report. Now each withdrawn work claim says it."""
    def claim(s, o, method):
        return {"type": "same-work", "subject": f"gutenberg:{s}", "object": f"gutenberg:{o}", "method": method}
    titles = dict(gutenberg_titles.METHOD)
    held = [ge.Held(claim(2, 1, titles), 0, None, ge.T_TITLES),      # volumes, by title
            ge.Held(claim(4, 3, ge.WIKIDATA), 1, None, ge.T_WIKIDATA)]
    made = [claim(1, 9, ge.WIKIDATA), claim(2, 9, ge.WIKIDATA)]       # now one Wikidata star: 1, 2 and 9
    d = ge.Derivation([(ge.T_WIKIDATA, c, ge._dumps(c)) for c in made], held,
                      {ge.T_WIKIDATA: True, ge.T_TITLES: True, ge.T_STAGE: False}, {},
                      frozenset(f"gutenberg:{n}" for n in (1, 2, 3, 4, 9)), {})
    tiers = ge.report(d)["tiers"]
    t = tiers[ge.T_TITLES]
    assert t["withdrawn"] == 1 and t["withdrawn_still_one_work"] == 1
    assert t["examples"]["withdrawn"][0]["now"] == "still one work through the claims made now"
    w = tiers[ge.T_WIKIDATA]
    assert w["withdrawn"] == 1 and "withdrawn_still_one_work" not in w
    assert w["examples"]["withdrawn"][0]["now"] == "apart: no claim made now joins the two"
