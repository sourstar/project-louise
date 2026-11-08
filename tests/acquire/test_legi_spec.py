"""LEGI's acquisition (app/acquire/specs.py LEGI_SPEC): the directory's own
index page as the listing (transport_https.list_index, the stdlib's
html.parser), its bytes charged to the budget (invariant 5), a transient
fault deferred and never a failure, a fetch of only the files not yet held —
all against a local mock server, never the origin."""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app import collections as collections_mod, db
from app.acquire import budget, engine, specs, transport_https
from app.acquire.spec import Endpoint, TransportError, Transport
from app.plugins.base import LicenseClass

SPEC = specs.LEGI_SPEC
NAMES = ("Freemium_legi_global_20250713-140000.tar.gz", "LEGI_20250714-213000.tar.gz",
         "LEGI_20261028-212713.tar.gz")

#: Apache's mod_autoindex, FancyIndexing on (its table form)
APACHE = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /OPENDATA/LEGI</title></head><body>
<h1>Index of /OPENDATA/LEGI</h1><table>
<tr><th valign="top"><img src="/icons/blank.gif" alt="[ICO]"></th><th><a href="?C=N;O=D">Name</a></th><th><a href="?C=M;O=A">Last modified</a></th><th><a href="?C=S;O=A">Size</a></th></tr>
<tr><th colspan="4"><hr></th></tr>
<tr><td valign="top"><img src="/icons/back.gif" alt="[PARENTDIR]"></td><td><a href="/OPENDATA/">Parent Directory</a></td><td>&nbsp;</td><td align="right">  - </td></tr>
<tr><td valign="top"><img src="/icons/compressed.gif" alt="[   ]"></td><td><a href="Freemium_legi_global_20250713-140000.tar.gz">Freemium_legi_global_20250713-140000.tar.gz</a></td><td align="right">2025-07-13 14:00  </td><td align="right">1.1G</td></tr>
<tr><td valign="top"><img src="/icons/compressed.gif" alt="[   ]"></td><td><a href="LEGI_20250714-213000.tar.gz">LEGI_20250714-213000.tar.gz</a></td><td align="right">2025-07-14 21:30  </td><td align="right">2.4M</td></tr>
<tr><td valign="top"><img src="/icons/unknown.gif" alt="[   ]"></td><td><a href="AVERTISSEMENT-Donnees_a_caractere_personnel.pdf">AVERTISSEMENT-Donnees_a_caractere_personnel.pdf</a></td><td align="right">2019-01-10 10:00  </td><td align="right">120K</td></tr>
<tr><td valign="top"><img src="/icons/folder.gif" alt="[DIR]"></td><td><a href="archives/">archives/</a></td><td align="right">2025-07-13 14:00  </td><td align="right">  - </td></tr>
</table></body></html>
"""

#: nginx's autoindex (its <pre> form, exact sizes)
NGINX = """<html>
<head><title>Index of /OPENDATA/LEGI/</title></head>
<body>
<h1>Index of /OPENDATA/LEGI/</h1><hr><pre><a href="../">../</a>
<a href="Freemium_legi_global_20250713-140000.tar.gz">Freemium_legi_global_20250713-140000.tar.gz</a>        13-Jul-2025 14:00          1171027316
<a href="LEGI_20261028-212713.tar.gz">LEGI_20261028-212713.tar.gz</a>                        28-Oct-2026 21:27             5138022
<a href="LEGI%2020261029.tar.gz">LEGI 20261029.tar.gz</a>                        29-Oct-2026 21:27             5138022
<a href="https://elsewhere.example/LEGI_20261030-000000.tar.gz">elsewhere</a>
</pre><hr></body>
</html>
"""


def test_apache_s_table_is_read_dates_kept_and_rounded_sizes_left_unknown():
    got = transport_https.parse_index(APACHE)
    assert [f.rel_path for f in got] == ["AVERTISSEMENT-Donnees_a_caractere_personnel.pdf",
                                         "Freemium_legi_global_20250713-140000.tar.gz",
                                         "LEGI_20250714-213000.tar.gz"], "no parent, no sort link, no folder"
    base = got[1]
    assert base.size is None, "1.1G is not a size: none rather than a wrong one"
    import calendar
    import time
    assert base.mtime == calendar.timegm(time.strptime("2025-07-13 14:00", "%Y-%m-%d %H:%M"))


def test_nginx_s_pre_is_read_with_its_exact_sizes():
    got = {f.rel_path: f for f in transport_https.parse_index(NGINX)}
    assert set(got) == {"Freemium_legi_global_20250713-140000.tar.gz", "LEGI_20261028-212713.tar.gz",
                        "LEGI 20261029.tar.gz"}, "another host's link is not this directory's"
    assert got["Freemium_legi_global_20250713-140000.tar.gz"].size == 1_171_027_316
    assert got["LEGI_20261028-212713.tar.gz"].size == 5_138_022


def test_the_spec_s_pattern_keeps_the_tarballs_alone():
    pattern = SPEC.inventory.pattern
    assert [f.rel_path for f in transport_https.parse_index(APACHE, pattern)] == list(NAMES[:2])
    assert [f.rel_path for f in transport_https.parse_index(NGINX, pattern)] == \
        ["Freemium_legi_global_20250713-140000.tar.gz", "LEGI_20261028-212713.tar.gz"]


def _serve(mock_server, page: str, files: dict[str, bytes]) -> Endpoint:
    d = mock_server.root / "OPENDATA" / "LEGI"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(page, encoding="utf-8")
    for name, data in files.items():
        (d / name).write_bytes(data)
    return replace(SPEC.endpoints[0], url=f"{mock_server.base_url}/OPENDATA/LEGI")


def test_the_listing_is_one_get_charged_to_the_budget(mock_server, monkeypatch):
    ep = _serve(mock_server, APACHE, {})
    charged = []
    monkeypatch.setattr(budget, "acquire_bytes", lambda n: charged.append(n))
    got = SPEC.inventory.list(ep, SPEC, None)         # the spec's Inventory, as the engine asks it
    assert [f.rel_path for f in got] == list(NAMES[:2])
    assert sum(charged) == len(APACHE.encode()), "every byte of the page drawn from the budget"
    assert mock_server.request_counts["OPENDATA/LEGI/"] == 1


def test_a_transient_fault_is_retried_then_deferred_never_failed(mock_server):
    ep = _serve(mock_server, APACHE, {})
    mock_server.statuses["OPENDATA/LEGI/"] = [503, 502]
    assert len(transport_https.list_index(ep, pattern=SPEC.inventory.pattern, backoff=0)) == 2, \
        "two 5xx, then the page"
    mock_server.statuses["OPENDATA/LEGI/"] = [500] * 5
    with pytest.raises(transport_https.ListingDeferred) as e:
        transport_https.list_index(ep, backoff=0)
    assert isinstance(e.value, httpx.TransportError), "the worker's deferral, not an error"
    assert engine.transient(str(e.value)), "and a transient one to the engine"


def test_a_missing_directory_is_the_endpoint_s_error(mock_server):
    ep = replace(SPEC.endpoints[0], url=f"{mock_server.base_url}/OPENDATA/NOWHERE")
    with pytest.raises(TransportError) as e:
        transport_https.list_index(ep, backoff=0)
    assert not isinstance(e.value, transport_https.ListingDeferred)


def test_a_static_endpoint_is_listed_as_before():
    ep = Endpoint(transport=Transport.HTTPS, url="https://h", static_files=())
    assert list(transport_https.HttpsTransport().list_files(ep, SPEC)) == [], \
        "the transport lists the declared files; an Inventory is the spec's, asked by the engine"


def test_a_pass_fetches_only_the_files_not_yet_held(tmp_path, mock_server):
    files = {NAMES[0]: b"baseline" * 100, NAMES[1]: b"increment-1" * 10}
    ep = _serve(mock_server, NGINX.replace("1171027316", str(len(files[NAMES[0]])))
                .replace("LEGI_20261028-212713", "LEGI_20250714-213000")
                .replace("5138022", str(len(files[NAMES[1]])), 1), files)
    spec = replace(SPEC, endpoints=(ep,))
    c = collections_mod.Collection(
        name="legi", plugin="legi", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "corpus", manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb", acquire_enabled=True, license_ack=None)
    c.corpus_dir.mkdir()
    con = db.connect(c)
    first = engine.run_acquisition(con, c, spec, "full")
    assert first["fetched"] == 2
    assert (c.corpus_dir / NAMES[0]).read_bytes() == files[NAMES[0]]
    # the next day: one more increment in the listing; only it moves
    new = "LEGI_20250715-213000.tar.gz"
    (mock_server.root / "OPENDATA" / "LEGI" / new).write_bytes(b"increment-2")
    page = (mock_server.root / "OPENDATA" / "LEGI" / "index.html").read_text(encoding="utf-8")
    page = page.replace("</pre>", f'<a href="{new}">{new}</a>   15-Jul-2025 21:30   11\n</pre>')
    (mock_server.root / "OPENDATA" / "LEGI" / "index.html").write_text(page, encoding="utf-8")
    before = dict(mock_server.request_counts)
    second = engine.run_acquisition(con, c, spec, "full")
    assert second["fetched"] == 1 and second["skipped"] == 2
    assert mock_server.request_counts.get(f"OPENDATA/LEGI/{NAMES[0]}") == before.get(f"OPENDATA/LEGI/{NAMES[0]}")
    assert (c.corpus_dir / new).read_bytes() == b"increment-2"


def test_the_spec_is_registered_green_with_two_connections_and_its_terms():
    assert specs.ACQUIRE_REGISTRY["legi"] is SPEC and SPEC.transport is Transport.HTTPS
    assert SPEC.license_class == LicenseClass.GREEN and SPEC.terms_text_path.is_file()
    assert SPEC.endpoints[0].max_conns == 2 and SPEC.etiquette.max_conns == 2
    assert isinstance(SPEC.inventory, transport_https.HtmlIndex) and SPEC.delta.mode == "full-resync"
    assert SPEC.unit_fetcher is None
    assert SPEC.endpoints[0].url == "https://echanges.dila.gouv.fr/OPENDATA/LEGI/"


def test_dila_s_own_apache_index_is_read_names_and_dates_never_a_rounded_size():
    """DILA's page as the node probed it (München, 2026-11-08,
    /root/legal-probe-20261108.log): Apache's `<pre>` autoindex, sort links
    `?C=N;O=D`, sizes rounded (`1.1G`, `1.7M`). A rounded size is no size:
    the HEAD gives the length (size_of), never `1.7M` read as bytes."""
    from app.acquire.transport_https import parse_index
    page = """<html><head><title>Index of /OPENDATA/LEGI</title></head><body>
<h1>Index of /OPENDATA/LEGI</h1><pre><img src="/icons/blank.gif" alt="Icon "> <a href="?C=N;O=D">Name</a>                    <a href="?C=M;O=A">Last modified</a>      <a href="?C=S;O=A">Size</a>  <hr><img src="/icons/back.gif" alt="[PARENTDIR]"> <a href="/OPENDATA/">Parent Directory</a>                             -
<img src="/icons/layout.gif" alt="[   ]"> <a href="DILA_LEGI_Presentation_20170824.pdf">DILA_LEGI_Presentation_20170824.pdf</a>  2017-08-24 10:00  300K
<img src="/icons/compressed.gif" alt="[   ]"> <a href="Freemium_legi_global_20250713-140000.tar.gz">Freemium_legi_global_20250713-140000.tar.gz</a> 2025-07-13 14:10  1.1G
<img src="/icons/compressed.gif" alt="[   ]"> <a href="LEGI_20261107-210930.tar.gz">LEGI_20261107-210930.tar.gz</a>  2026-11-07 21:12  1.7M
<hr></pre></body></html>"""
    got = parse_index(page, SPEC.inventory.pattern)
    assert [f.rel_path for f in got] == ["Freemium_legi_global_20250713-140000.tar.gz",
                                         "LEGI_20261107-210930.tar.gz"], "the PDF and the sort links are not files"
    assert all(f.size is None for f in got), "a rounded size is never taken for bytes"
    assert all(f.mtime for f in got)
