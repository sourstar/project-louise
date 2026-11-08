"""NeuRIS's acquisition (app/acquire/neuris_listing.py; NEURIS_SPEC), against
a LOCAL mock of the API only — never the real origin: the paginated
listing complete or raising, two files per expression under its ELI, the
delta the listing compared with what is held by path, the changelog's
`allChanged` obeyed, `changed` fetched again and `deleted` reported and
never removed, a 5xx retried then deferred, every page charged to the
budget, and the pair discovered by the plugin as it landed."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import db
from app.acquire import engine, specs, transport_https
from app.acquire import neuris_listing as nl
from app.acquire.spec import Endpoint, Transport
from app.plugins.base import DiscoverState
from app.plugins.neuris import SOURCE
from tests import neuris_support as ns
from tests.acquire.test_engine import _collection

#: a fixed "now": 2026-11-08 12:00 UTC
NOW = 1_794_139_200.0


class Api:
    """The API's state: the expressions listed, what the changelog says per
    day, and faults to inject."""

    def __init__(self, expressions):
        self.items = {e: ns.listing_item(e) for e in expressions}
        self.changelog = {}            # "YYYY-MM-DD" -> answer
        self.fail = {}                 # path prefix -> remaining 503s
        self.hits: dict[str, int] = {}
        self.lock = threading.Lock()
        self.zips_built = 0
        self.total_override = None

    def regenerate(self, expression, generated):
        """The manifestation rebuilt: the listing's encodings name a new date."""
        item = self.items[expression]
        old = ns.directory(expression)[0]
        item["encoding"] = [dict(enc, contentUrl=enc["contentUrl"].replace(f"/{old}", f"/{generated}"),
                                 **{"@id": enc["@id"].replace(f"/{old}", f"/{generated}")})
                            for enc in item["encoding"]]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, body: bytes, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        api: Api = self.server.api      # type: ignore[attr-defined]
        url = urlparse(self.path)
        path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        with api.lock:
            api.hits[path] = api.hits.get(path, 0) + 1
            for prefix, n in list(api.fail.items()):
                if path.startswith(prefix) and n > 0:
                    api.fail[prefix] = n - 1
                    return self._send(503, b"Service Unavailable", "text/plain")
        if path == "/v1/legislation":
            size, page = int(q.get("size", 100)), int(q.get("pageIndex", 0))
            assert q.get("sort") == "legislationIdentifier"
            items = [api.items[k] for k in sorted(api.items)]
            members = [{"@type": "SearchResult", "item": i, "textMatches": []}
                       for i in items[page * size:(page + 1) * size]]
            total = api.total_override if api.total_override is not None else len(items)
            body = {"@type": "hydra:Collection", "@context": "x", "@id": self.path,
                    "totalItems": total, "member": members,
                    "view": {"@type": "hydra:PartialCollectionView"}}
            return self._send(200, json.dumps(body).encode())
        if path == "/v1/legislation/changelog":
            day = q["from"][:10]
            body = api.changelog.get(day, {"@type": "x", "@context": "x", "changed": [], "deleted": [],
                                           "allChanged": True})
            return self._send(200, json.dumps(body).encode())
        rest = path[len("/v1/legislation/"):]
        for e in api.items:
            if rest == e:
                return self._send(200, json.dumps(ns.api_json(e), ensure_ascii=False).encode())
            if rest.startswith(e + "/") and rest.endswith(".zip"):
                with api.lock:
                    api.zips_built += 1
                    n = api.zips_built
                # built on the fly: every answer another zip of the same members
                return self._send(200, ns.zip_bytes(e, date_time=(2026, 11, 8, 12, n % 60, 0)),
                                  "application/zip")
        self._send(404, b"{}")


@pytest.fixture
def api_server():
    api = Api([ns.S1016, *ns.S1234, ns.N130])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.api = api                       # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _spec(base, **listing_kw):
    listing = nl.NeurisListing(now=lambda: NOW, sleep=lambda s: None, **listing_kw)
    return replace(specs.NEURIS_SPEC, endpoints=(Endpoint(transport=Transport.HTTPS, url=base, max_conns=2,
                                                          listing=listing),))


def _pass(tmp_path, base, **kw):
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    return collection, con, engine.run_acquisition(con, collection, _spec(base, **kw), "full")


def _warned(con):
    return [r[0] for r in con.execute("SELECT message FROM events WHERE stage='acquire' AND level='warn'")]


def test_the_listing_is_paged_to_its_end_and_each_expression_lands_as_its_pair(tmp_path, api_server):
    api, base = api_server
    collection, con, got = _pass(tmp_path, base, page_size=2)
    assert got["fetched"] == 10 and got["failed"] == 0 and got["total"] == 10
    assert api.hits["/v1/legislation"] == 3, "five expressions, two a page"
    for e in api.items:
        gen = ns.directory(e)[0]
        assert (collection.corpus_dir / e / f"{gen}.zip").is_file()
        assert json.loads((collection.corpus_dir / e / f"{gen}.json").read_bytes()) == ns.api_json(e)
    refs = list(SOURCE.discover(DiscoverState(corpus_root=collection.corpus_dir)))
    assert len(refs) == 5 and all(r.error is None for r in refs)
    want = {e: SOURCE.fetch(r).raw_hash for e, r in
            (("/".join(r.locator.split("/")[-9:-1]), r) for r in refs)}
    assert len(set(want.values())) == 5


def test_an_unchanged_listing_moves_nothing_and_allchanged_leaves_the_comparison(tmp_path, api_server):
    api, base = api_server
    collection, con, first = _pass(tmp_path, base)
    zips = api.zips_built
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert (got["fetched"], got["skipped"]) == (0, 10), "every path held: skipped"
    assert api.zips_built == zips, "no zip asked for again"
    assert api.hits["/v1/legislation/changelog"] == 2 * nl.CHANGELOG_DAYS, "read every pass, never trusted empty"


def test_a_regenerated_manifestation_is_a_new_path_and_the_old_stays(tmp_path, api_server):
    api, base = api_server
    collection, con, _ = _pass(tmp_path, base)
    api.regenerate(ns.N130, "2026-11-07")
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 2 and got["skipped"] == 8
    d = collection.corpus_dir / ns.N130
    assert sorted(p.name for p in d.iterdir()) == ["2025-08-31.json", "2025-08-31.zip",
                                                   "2026-11-07.json", "2026-11-07.zip"]
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=collection.corpus_dir))
            if ns.N130 in r.locator]
    assert [r.locator.rsplit("/", 1)[-1] for r in refs] == ["2026-11-07.zip"], "the latest is the record's"


def test_the_changelog_when_it_is_a_delta_refetches_what_changed_and_reports_what_was_deleted(tmp_path, api_server):
    api, base = api_server
    collection, con, _ = _pass(tmp_path, base)
    held = collection.corpus_dir / ns.S1016 / "2023-04-26.zip"
    assert held.is_file()
    api.changelog["2026-11-08"] = {
        "@type": "x", "@context": "x", "allChanged": False,
        "changed": [{"@id": f"/v1/legislation/{ns.N130}", "@type": "Legislation",
                     "contentUrl": f"/v1/legislation/{ns.N130}/2025-08-31/regelungstext-1.xml"}],
        "deleted": [{"@id": f"/v1/legislation/{ns.S1016}", "@type": "Legislation"}]}
    zips = api.zips_built
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 2 and api.zips_built == zips + 1, "the changed expression's pair, again"
    stamp = con.execute("SELECT remote_mtime FROM fetch_files WHERE rel_path=?",
                        (f"{ns.N130}/2025-08-31.zip",)).fetchone()[0]
    assert stamp == nl.day_end("2026-11-08")
    assert held.is_file(), "a deletion is reported, never acted on"
    assert any("deleted" in m and ns.S1016 in m for m in _warned(con))
    again = engine.run_acquisition(con, collection, _spec(base), "full")
    assert again["fetched"] == 0, "the stamp is the changelog's own answer: stable while the day is read"


def test_a_5xx_on_the_listing_is_retried_then_defers_the_pass(tmp_path, api_server):
    api, base = api_server
    api.fail["/v1/legislation"] = 2
    _, _, got = _pass(tmp_path, base)
    assert got["fetched"] == 10, "two 503s, then the answer"
    api.fail["/v1/legislation"] = 10_000
    with pytest.raises(httpx.TransportError) as e:          # the worker's deferral branch
        _pass(tmp_path / "b", base)
    assert isinstance(e.value, nl.ListingDeferred)


def test_a_5xx_on_a_file_is_the_origins_episode(tmp_path, api_server, monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)       # the stream retries, not waited out
    api, base = api_server
    api.fail[f"/v1/legislation/{ns.N130}/"] = 10_000
    collection, con, got = _pass(tmp_path, base)
    assert got["failed"] == 1 and got["fetched"] == 9
    row = con.execute("SELECT error, retries, fetch_done FROM fetch_files WHERE rel_path=?",
                      (f"{ns.N130}/2025-08-31.zip",)).fetchone()
    assert (row["error"], row["retries"], row["fetch_done"]) == (None, 1, 0), \
        "a deferral: no error written, the next pass asks again"
    zip_f = nl.files_of(api.items[ns.N130])[0]
    with pytest.raises(Exception) as e:
        transport_https.HttpsTransport().fetch_one(Endpoint(transport=Transport.HTTPS, url=base), zip_f,
                                                   tmp_path / "x.zip", 0)
    assert "503" in str(e.value) and engine.transient(str(e.value)), str(e.value)


def test_a_listing_that_moved_or_overflows_is_never_truncated(tmp_path, api_server):
    api, base = api_server
    api.total_override = 7
    with pytest.raises(nl.ListingDeferred, match="distinct expressions"):
        _pass(tmp_path, base)
    api.total_override = nl.WINDOW + 1
    with pytest.raises(Exception, match="window"):
        _pass(tmp_path / "b", base)


def test_a_changelog_that_cannot_be_read_leaves_the_comparison_and_says_so(tmp_path, api_server):
    api, base = api_server
    api.fail["/v1/legislation/changelog"] = 10_000
    collection, con, got = _pass(tmp_path, base)
    assert got["fetched"] == 10
    assert any(m.startswith("changelog") and "unread" in m for m in _warned(con))


def test_every_page_is_charged_to_the_budget(tmp_path, api_server, monkeypatch):
    api, base = api_server
    charged = []
    monkeypatch.setattr(nl.budget, "acquire_bytes", lambda n: charged.append(n))
    listing = nl.NeurisListing(now=lambda: NOW, sleep=lambda s: None, page_size=2)
    files = list(listing(Endpoint(transport=Transport.HTTPS, url=base), specs.NEURIS_SPEC))
    assert len(files) == 10 and len(charged) == 3 + nl.CHANGELOG_DAYS and all(n > 0 for n in charged)


def test_the_two_files_of_an_expression():
    zip_f, json_f = nl.files_of(ns.listing_item(ns.S1016))
    assert zip_f.rel_path == f"{ns.S1016}/2023-04-26.zip"
    assert zip_f.remote == f"v1/legislation/{ns.S1016}/2023-04-26.zip"
    assert json_f.rel_path == f"{ns.S1016}/2023-04-26.json" and json_f.remote == f"v1/legislation/{ns.S1016}"
    assert transport_https._url(Endpoint(transport=Transport.HTTPS, url="https://x/"), zip_f) == \
        f"https://x/v1/legislation/{ns.S1016}/2023-04-26.zip"
    # no zip encoding: the API's zip route for the XML's manifestation
    item = ns.listing_item(ns.S1016)
    item["encoding"] = [e for e in item["encoding"] if e["encodingFormat"] != "application/zip"]
    assert nl.files_of(item)[0].remote == f"v1/legislation/{ns.S1016}/2023-04-26.zip"
    assert isinstance(nl.files_of({"legislationIdentifier": "eli/../x"}), str), "reported, never placed"


def test_a_head_is_never_asked_of_an_api_listed_origin():
    ep = specs.NEURIS_SPEC.endpoints[0]
    assert transport_https.HttpsTransport().size_of(ep, nl.files_of(ns.listing_item(ns.S1016))[0]) is None
