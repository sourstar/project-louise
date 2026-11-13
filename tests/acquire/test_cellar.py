"""Cellar as an acquisition origin (app/acquire/cellar.py; engine
the engine's generic `_unit_fetch` through `cellar.UNITS`; legal-fr-de-eu.md §4.1, §7.1), against a LOCAL mock of
its two faces: the SPARQL endpoint (SPARQL 1.1 Protocol, JSON results) and
the content negotiation on `/resource/celex/<CELEX>` — 303 to the branch
notice, the items by the names the notice gives, or a 300 Multiple Choices
listing them. Never the real origin.

The Formex items are the real fixtures (tests/fixtures/eurlex); the French
unit is the English Formex with its LG.DOC set to FR, built here to stand
for a second language (none was found on GitHub; the README)."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from types import SimpleNamespace

import httpx
import pytest

from app import db
from app.acquire import cellar, engine, specs
from app.acquire.spec import Endpoint, Filter, TransportError, Transport
from app.plugins.eurlex import source as src
from tests.acquire.test_engine import _collection

FORMEX = Path(__file__).resolve().parent.parent / "fixtures" / "eurlex" / "formex"
#: eurlex is YELLOW (operator, 2026-11-08, A): a pass moves no byte before the
#: acknowledgement of its terms (invariant 9), as a peer records it
_ACK = engine.make_ack(specs.EURLEX_SPEC.terms_text_path)
ACT, CONS = "32016R1036", "02016R1036-20180608"


def _formex(celex_dir: str, lang: str) -> dict[str, bytes]:
    files = {p.name: p.read_bytes() for p in sorted((FORMEX / celex_dir).iterdir())}
    if lang != "en":
        files = {n: b.replace(b"<LG.DOC>EN</LG.DOC>", f"<LG.DOC>{lang.upper()}</LG.DOC>".encode())
                 for n, b in files.items()}
    return files


def _notice(base: str, celex: str, lang: str, files: dict[str, bytes] | None) -> bytes:
    """A branch notice in Cellar's shape (NOTICE/WORK, NOTICE/EXPRESSION,
    NOTICE/MANIFESTATION with its items' TECHMD), naming the items when
    `files` is given."""
    code = src.LANGUAGES[lang]
    items = "".join(
        f"<MANIFESTATION_HAS_ITEM><URI><VALUE>{base}/items/{celex}/{lang}/DOC_{i}</VALUE><TYPE>cellar</TYPE></URI>"
        f"<TECHMD><STREAM_NAME type=\"data\"><VALUE>{name}</VALUE></STREAM_NAME>"
        f"<STREAM_SIZE type=\"data\"><VALUE>{len(data)}</VALUE></STREAM_SIZE>"
        f"<STREAM_ORDER type=\"data\"><VALUE>{i}</VALUE></STREAM_ORDER></TECHMD></MANIFESTATION_HAS_ITEM>"
        for i, (name, data) in enumerate(reversed(list((files or {}).items())), 1))
    return (f'<NOTICE decoding="{code.lower()}" type="branch"><WORK><RESOURCE_LEGAL_ID_CELEX type="data">'
            f'<VALUE>{celex}</VALUE></RESOURCE_LEGAL_ID_CELEX></WORK><EXPRESSION><EXPRESSION_USES_LANGUAGE '
            f'type="concept"><IDENTIFIER>{code}</IDENTIFIER></EXPRESSION_USES_LANGUAGE></EXPRESSION>'
            f'<MANIFESTATION manifestation-type="fmx4">{items}</MANIFESTATION></NOTICE>').encode()


class Cellar:
    """The mock: `units[(celex, lang)] = {"files": …, "modified": iso, "listed": bool}`."""

    def __init__(self) -> None:
        self.units: dict[tuple[str, str], dict] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self.fail: dict[str, int] = {}          # path prefix → 503s still to answer
        self.short_pages = False                # the pages answer fewer rows than the count
        self.lock = threading.Lock()
        owner = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _failing(self) -> bool:
                with owner.lock:
                    for prefix, n in owner.fail.items():
                        if self.path.startswith(prefix) and n > 0:
                            owner.fail[prefix] = n - 1
                            return True
                return False

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                query = parse_qs(self.rfile.read(n).decode())["query"][0]
                with owner.lock:
                    owner.requests.append(("POST", self.path, {"query": query}))
                if self._failing():
                    return self._send(503)
                year = re.search(r'STRSTARTS\(STR\(\?celex\), "3(\d{4})"\)', query).group(1)
                langs = {src.notice_mod.CODE_OF[c] for c in re.findall(r"language/([A-Z]{3})>", query)}
                rows = sorted((c, lg) for (c, lg), u in owner.units.items()
                              if u.get("listed", True) and c[1:5] == year and lg in langs)
                if "COUNT(" in query:
                    body = {"results": {"bindings": [{"n": {"value": str(len(rows))}}]}}
                else:
                    off = int(re.search(r"OFFSET (\d+)", query).group(1))
                    lim = int(re.search(r"LIMIT (\d+)", query).group(1))
                    page = rows[off:off + lim]
                    if owner.short_pages:
                        page = page[:-1] if off == 0 else []
                    body = {"results": {"bindings": [
                        {"celex": {"type": "literal", "value": c},
                         "language": {"type": "uri",
                                      "value": f"http://publications.europa.eu/resource/authority/language/"
                                               f"{src.LANGUAGES[lg]}"},
                         "last": {"type": "literal", "value": owner.units[(c, lg)]["modified"]}}
                        for c, lg in page]}}
                self._send(200, json.dumps(body).encode(), {"Content-Type": "application/sparql-results+json"})

            def do_GET(self):
                with owner.lock:
                    owner.requests.append(("GET", self.path, dict(self.headers)))
                if self._failing():
                    return self._send(503)
                path = urlparse(self.path).path
                base = f"http://127.0.0.1:{owner.port}"
                if path.startswith("/resource/celex/"):
                    celex = unquote(path.rsplit("/", 1)[1])
                    lang = src.notice_mod.CODE_OF.get(self.headers.get("Accept-Language", "").upper())
                    unit = owner.units.get((celex, lang))
                    if unit is None:
                        return self._send(404)
                    accept = self.headers.get("Accept", "")
                    if "notice=branch" in accept:
                        return self._send(303, headers={"Location": f"{base}/notices/{celex}/{lang}"})
                    if "type=fmx4" in accept:
                        links = "".join(f'<li><a href="{base}/items/{celex}/{lang}/DOC_{i}">{n}</a></li>'
                                        for i, n in enumerate(reversed(list(unit["files"])), 1))
                        return self._send(300, f"<html><body><ul>{links}</ul></body></html>".encode(),
                                          {"Content-Type": "text/html"})
                    return self._send(406)
                if path.startswith("/notices/"):
                    _, _, celex, lang = path.split("/")
                    unit = owner.units[(celex, lang)]
                    return self._send(200, _notice(base, celex, lang, None if unit.get("no_items") else unit["files"]),
                                      {"Content-Type": "application/xml"})
                if path.startswith("/items/"):
                    _, _, celex, lang, doc = path.split("/")
                    names = list(reversed(list(owner.units[(celex, lang)]["files"])))
                    name = names[int(doc.split("_")[1]) - 1]
                    return self._send(200, owner.units[(celex, lang)]["files"][name],
                                      {"Content-Type": "application/xml"})
                self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, celex: str, lang: str, celex_dir: str, modified: str = "2018-06-08T10:00:00Z", **kw) -> None:
        self.units[(celex, lang)] = {"files": _formex(celex_dir, lang), "modified": modified, **kw}

    def gets(self, prefix: str) -> list:
        return [r for r in self.requests if r[0] == "GET" and r[1].startswith(prefix)]

    def queries(self) -> list[str]:
        return [r[2]["query"] for r in self.requests if r[0] == "POST"]


@pytest.fixture
def mock():
    m = Cellar()
    for lang in ("en", "fr", "de"):
        m.add(ACT, lang, ACT)
        m.add(CONS, lang, CONS)
    yield m
    m.server.shutdown()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(cellar.time, "sleep", lambda s: None)


def _spec(mock):
    """The plugin's spec, its endpoint the mock."""
    import dataclasses
    real = specs.EURLEX_SPEC
    return dataclasses.replace(real, endpoints=(Endpoint(transport=Transport.HTTPS, url=mock.base, max_conns=2),))


def _years(monkeypatch):
    monkeypatch.setattr(cellar, "FIRST_YEAR", 2016)
    import time as _t
    monkeypatch.setattr(cellar.time, "gmtime", lambda *a: _t.struct_time((2016, 1, 1, 0, 0, 0, 4, 1, 0)))


# ---- the inventory --------------------------------------------------------------------------

def test_the_listing_asks_only_the_profile_s_languages_counted_then_paged(mock):
    spec = _spec(mock)
    got = list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=fr/*",)), years=range(2016, 2017)))
    assert sorted(f.rel_path for f in got) == [f"lang=fr/2016/{CONS}/notice.xml", f"lang=fr/2016/{ACT}/notice.xml"]
    assert all(cellar.is_unit(f) and cellar.unit_of(f)["lang"] == "fr" for f in got)
    assert all(f.mtime == 1528452000 for f in got), "the work's last modification, as the listing's date"
    q = mock.queries()
    assert "COUNT(" in q[0] and "language/FRA>" in q[0] and "language/ENG" not in "".join(q)
    assert "OFFSET 0" in q[1] and 'STRSTARTS(STR(?celex), "02016")' in q[1]


def test_every_page_is_read_to_the_window_s_count(mock, monkeypatch):
    monkeypatch.setattr(cellar, "PAGE", 1)
    spec = _spec(mock)
    got = list(cellar.listing(spec.endpoints[0], spec, Filter(), years=range(2016, 2017)))
    assert len(got) == 6 and len(mock.queries()) == 1 + 6


def test_a_window_cut_short_is_the_origin_s_episode(mock):
    mock.short_pages = True
    spec = _spec(mock)
    with pytest.raises(TransportError, match="answered 1 of the 2 rows") as e:
        list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=en/*",)), years=range(2016, 2017)))
    assert engine.transient(str(e.value)), "deferred, never the file's error"


def test_a_5xx_is_retried_then_raised_as_a_deferral(mock):
    spec = _spec(mock)
    mock.fail["/webapi"] = 2
    assert len(list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=de/*",)),
                                   years=range(2016, 2017)))) == 2
    mock.fail["/webapi"] = 99
    with pytest.raises(TransportError) as e:
        list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=de/*",)), years=range(2016, 2017)))
    assert engine.transient(str(e.value)) and " 503 to " in str(e.value)


def test_languages_of_a_filter():
    assert cellar.languages_of(Filter(include=("lang=fr/*", "lang=de/*"))) == ("de", "fr")
    assert len(cellar.languages_of(Filter())) == 24


# ---- one unit -------------------------------------------------------------------------------

def test_a_unit_lands_whole_its_items_by_the_notice_s_names(mock, tmp_path):
    charged: list[int] = []
    dest = tmp_path / src.unit_rel("en", ACT)
    got = cellar.fetch_unit(mock.base, ACT, "en", dest, charge=charged.append)
    want = _formex(ACT, "en")
    assert sorted(p.name for p in dest.iterdir()) == sorted([*want, "notice.xml"])
    assert all((dest / n).read_bytes() == b for n, b in want.items())
    assert sum(charged) == got.moved == sum(len(b) for b in want.values()) + (dest / "notice.xml").stat().st_size
    assert not list(dest.parent.glob(".*")), "no staging left behind"
    first = mock.gets("/resource/celex/")[0][2]
    assert first["Accept"] == "application/xml;notice=branch" and first["Accept-Language"] == "eng"


def test_without_items_in_the_notice_the_300_lists_them_by_name(mock, tmp_path):
    mock.units[(CONS, "en")]["no_items"] = True
    dest = tmp_path / src.unit_rel("en", CONS)
    cellar.fetch_unit(mock.base, CONS, "en", dest)
    assert sorted(p.name for p in dest.iterdir()) == sorted([*_formex(CONS, "en"), "notice.xml"])
    assert any("type=fmx4" in h.get("Accept", "") for _, _, h in mock.gets("/resource/celex/"))


def test_the_300_s_names_are_read_from_its_links():
    body = (b'<html><a href="http://x/resource/cellar/u.0022.01/DOC_2">CL2016R0679EN0000020.0001.doc.xml</a>'
            b'<a href="http://x/resource/cellar/u.0022.01/DOC_1">CL2016R0679EN0000020.0001.xml</a>'
            b' and http://x/resource/cellar/u.0022.01/DOC_3</html>')
    assert cellar.items_from_choices(body) == [
        ("http://x/resource/cellar/u.0022.01/DOC_2", "CL2016R0679EN0000020.0001.doc.xml"),
        ("http://x/resource/cellar/u.0022.01/DOC_1", "CL2016R0679EN0000020.0001.xml"),
        ("http://x/resource/cellar/u.0022.01/DOC_3", None)]


def test_a_refetch_replaces_the_unit_and_drops_what_the_origin_no_longer_lists(mock, tmp_path):
    dest = tmp_path / src.unit_rel("en", ACT)
    cellar.fetch_unit(mock.base, ACT, "en", dest)
    (dest / "stale.xml").write_bytes(b"<x/>")
    cellar.fetch_unit(mock.base, ACT, "en", dest)
    assert not (dest / "stale.xml").exists()


def test_another_language_than_asked_is_refused_and_is_the_file_s_failure(mock, tmp_path):
    mock.units[(ACT, "fr")]["files"] = _formex(ACT, "en")          # Cellar answering English
    with pytest.raises(TransportError, match="its Formex is in en") as e:
        cellar.fetch_unit(mock.base, ACT, "fr", tmp_path / src.unit_rel("fr", ACT))
    assert not engine.transient(str(e.value))
    assert not (tmp_path / src.unit_rel("fr", ACT)).exists(), "nothing lands"


def test_an_item_of_another_size_than_the_notice_says_is_refused(mock, tmp_path, monkeypatch):
    original = cellar._get

    def short(client, url, headers, what, **kw):
        r = original(client, url, headers, what, **kw)
        if "/items/" in url and url.endswith("DOC_1"):
            import httpx
            return httpx.Response(200, content=r.content[:-1], headers=r.headers)
        return r
    monkeypatch.setattr(cellar, "_get", short)
    with pytest.raises(TransportError, match="the notice says"):
        cellar.fetch_unit(mock.base, ACT, "en", tmp_path / src.unit_rel("en", ACT))


# ---- through the engine ---------------------------------------------------------------------

def test_a_node_holding_only_french_fetches_only_french(tmp_path, mock, monkeypatch):
    _years(monkeypatch)
    collection = _collection(tmp_path, plugin="eurlex", license_ack=_ACK)
    con = db.connect(collection)
    spec = _spec(mock)
    got = engine.run_acquisition(con, collection, spec, "fr")
    assert got["fetched"] == 2 and got["failed"] == 0
    assert sorted(p.relative_to(collection.corpus_dir).as_posix()
                  for p in collection.corpus_dir.glob("lang=*/*/*")) == [
        f"lang=fr/2016/{CONS}", f"lang=fr/2016/{ACT}"]
    assert {h.get("Accept-Language") for _, _, h in mock.gets("/resource/celex/")} == {"fra"}
    # the listing compared with what is held: nothing moved, nothing fetched
    before = len(mock.gets("/"))
    again = engine.run_acquisition(con, collection, spec, "fr")
    assert again["fetched"] == 0 and len(mock.gets("/")) == before
    # a work Cellar modified is fetched again, and only it
    mock.units[(ACT, "fr")]["modified"] = "2026-11-01T00:00:00Z"
    third = engine.run_acquisition(con, collection, spec, "fr")
    assert third["fetched"] == 1
    # and the plugin discovers what landed, in French
    from app.plugins.base import DiscoverState
    from app.plugins.eurlex import SOURCE
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=collection.corpus_dir)) if r.error is None]
    assert len(refs) == 2 and all("/lang=fr/" in r.locator for r in refs)


def test_a_language_added_later_fetches_that_language_alone(tmp_path, mock, monkeypatch):
    _years(monkeypatch)
    collection = _collection(tmp_path, plugin="eurlex", license_ack=_ACK)
    con = db.connect(collection)
    spec = _spec(mock)
    engine.run_acquisition(con, collection, spec, "fr")
    n = len(mock.gets("/resource/celex/"))
    got = engine.run_acquisition(con, collection, spec, "fr,de")
    assert got["fetched"] == 2
    new = mock.gets("/resource/celex/")[n:]
    assert {h.get("Accept-Language") for _, _, h in new} == {"deu"}, "nothing French fetched again"


def test_records_vs_origin_refetches_one_unit_by_its_cellar_uri(mock):
    """Through the generic unit origin (app/acquire/unit_origin.py): the spec
    declares a UnitFetcher with `refetch`, and nothing names the plugin."""
    from app.acquire import unit_origin
    origin = unit_origin.UnitOrigin(_spec(mock))
    try:
        out = origin.materialise(f"lang=de/2016/{ACT}")
        assert sorted(p.name for p in out.iterdir()) == sorted([*_formex(ACT, "de"), "notice.xml"])
        assert origin.unit(f"lang=de/2016/{ACT}") == (f"lang=de/2016/{ACT}", None)
        assert origin.bytes_read > 0
        with pytest.raises(FileNotFoundError):
            origin.materialise("lang=de/2016/39999R9999")
    finally:
        origin.close()
    from types import SimpleNamespace
    assert isinstance(unit_origin.for_codex(SimpleNamespace(plugin="eurlex")), unit_origin.UnitOrigin)
    assert unit_origin.for_codex(SimpleNamespace(plugin="normattiva")) is None


# ---- a slow window (München, 2026-11-14: 1990's count in 235.5 s, 2021's in 45.9 s) ---------

def test_a_sparql_query_waits_past_a_download_s_bound_and_its_error_names_the_window(monkeypatch):
    """The query's own bound, not TIMEOUT_S; a timeout says which window."""
    seen = {}

    class Client:
        def stream(self, method, url, **kw):
            seen["timeout"] = kw.get("timeout")
            raise httpx.ReadTimeout("slow")
    with pytest.raises(TransportError) as e:
        cellar.sparql(Client(), "http://x", "SELECT 1", what="the count of 1990", charge=lambda n: None)
    assert seen["timeout"].read == cellar.SPARQL_TIMEOUT_S > 235.5 * 3 > cellar.TIMEOUT_S
    assert "the count of 1990" in str(e.value) and engine.transient(str(e.value))


def test_a_stop_ends_a_long_query_s_wait_at_once(monkeypatch):
    """A Virtuoso query evaluates before its first byte: the wait is in a
    thread, and a stop (SIGTERM, node pause) is honoured within a poll."""
    import threading
    import time as _t

    from app.acquire.spec import TransferAborted
    stop = threading.Event()
    monkeypatch.setattr(cellar, "current_handle", lambda: SimpleNamespace(stop=stop))
    release = threading.Event()
    threading.Timer(0.2, stop.set).start()
    t0 = _t.monotonic()
    with pytest.raises(TransferAborted):
        cellar._answer_of(lambda: release.wait(30), poll_s=0.05)
    assert _t.monotonic() - t0 < 5
    release.set()


def test_the_listing_names_each_query_it_asks(mock):
    spec = _spec(mock)
    mock.fail["/webapi"] = 99
    with pytest.raises(TransportError, match="to the count of 2016"):
        list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=de/*",)), years=range(2016, 2017)))


def test_a_window_whose_count_times_out_is_split_by_language_not_asked_again(mock, monkeypatch):
    """The architecture session, 2026-11-14: split a timed-out query rather
    than repeat it. Measured: 1990's count for all 24 languages ran past
    900 s, one language answered."""
    real = cellar.sparql
    asked: list[int] = []

    def slow_for_many(client, base, query, **kw):
        n = len(re.findall(r"language/[A-Z]{3}>", query))
        if "COUNT(" in query:
            asked.append(n)
            if n > 1:
                raise TransportError("cellar: connection timed out on the count: ReadTimeout after 900 s")
        return real(client, base, query, **kw)
    monkeypatch.setattr(cellar, "sparql", slow_for_many)
    spec = _spec(mock)
    got = list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=en/*", "lang=fr/*", "lang=de/*")),
                              years=range(2016, 2017)))
    assert len(got) == 6, "every language's files, each listed on its own"
    assert asked == [3, 1, 2, 1, 1], "all three once, then halves down to one language: never the same query again"


def test_one_language_whose_count_times_out_is_deferred(mock, monkeypatch):
    def always_slow(client, base, query, **kw):
        raise TransportError("cellar: connection timed out on the count: ReadTimeout")
    monkeypatch.setattr(cellar, "sparql", always_slow)
    spec = _spec(mock)
    with pytest.raises(TransportError) as e:
        list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=it/*",)), years=range(2016, 2017)))
    assert engine.transient(str(e.value))
