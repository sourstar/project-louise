"""The Met's Open Access plugin (app/plugins/met, app/acquire/met.py;
docs/notes/image-collections.md §8–§12). No network: the fixtures are three
real API answers (2026-11-28) and tiny stand-in images under their names."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import collections as collections_mod
from app.acquire import met as acquire_met
from app.acquire.spec import Endpoint, RemoteFile, Transport, TransportError
from app.acquire.specs import ACQUIRE_REGISTRY
from app.plugins.base import DiscoverState, LicenseClass
from app.plugins.met import SOURCE
from app.plugins.met import source as met_source

CORPUS = Path(__file__).parent / "fixtures" / "met" / "corpus"


def _docs(root=CORPUS):
    return {int(r.native_id): r for r in SOURCE.discover(DiscoverState(corpus_root=root))}


def _parse(oid, root=CORPUS):
    return SOURCE.parse(SOURCE.fetch(_docs(root)[oid]))


def test_registered_as_plugin_and_acquisition_spec():
    assert collections_mod.PLUGIN_REGISTRY["met"] is SOURCE
    spec = ACQUIRE_REGISTRY["met"]
    assert spec.source_id == "met" and spec.license_class is LicenseClass.GREEN
    assert spec.inventory is acquire_met.LISTING and spec.unit_fetcher is acquire_met.UNITS
    # the Met asks for at most 80 requests a second: this spec keeps far under it
    assert spec.etiquette.max_conns * (1 / spec.etiquette.min_interval_s) <= 80


def test_discover_every_landed_object_in_id_order():
    refs = list(SOURCE.discover(DiscoverState(corpus_root=CORPUS)))
    assert [r.native_id for r in refs] == ["1", "45734", "436535"]
    assert all(r.locator.endswith("/object.json") and r.error is None for r in refs)


def test_discover_skips_a_unit_whose_json_has_not_landed(tmp_path):
    shutil.copytree(CORPUS, tmp_path / "c")
    (tmp_path / "c/objects/0045/45734/object.json").rename(tmp_path / "c/objects/0045/45734/object.json.staged")
    assert sorted(_docs(tmp_path / "c")) == [1, 436535]


def test_uid_native_id_and_origin_hash_over_the_json_as_served():
    doc = _parse(436535)
    data = (CORPUS / "objects/0436/436535/object.json").read_bytes()
    assert doc.uid == "met:436535"
    assert doc.raw_hash == hashlib.blake2b(data, digest_size=16).hexdigest()


def test_record_fields_from_the_mets_own_json():
    doc = _parse(436535)
    obj = json.loads((CORPUS / "objects/0436/436535/object.json").read_bytes())
    assert doc.title == "Wheat Field with Cypresses"
    assert doc.creators == ["Vincent van Gogh"]
    assert doc.date == "1889"
    assert doc.language == "und"
    assert doc.subjects == [t["term"] for t in obj["tags"]]
    assert doc.keys == []
    # every key the API gives is kept, the mapped ones too (invariant 11)
    assert set(doc.extra) == set(met_source.EXTRA_KEYS)
    for k in met_source.API_KEYS:
        assert doc.extra[k] == obj.get(k)
    assert doc.extra["unlisted"] == {}
    assert doc.extra["isPublicDomain"] is True and "rightsAndReproduction" in doc.extra
    [(heading, text)] = doc.sections
    assert heading == "" and text.startswith("Wheat Field with Cypresses\n")
    assert "Artist: Vincent van Gogh" in text and "\r" not in text
    assert all(line == line.rstrip() for line in text.split("\n"))


def test_figures_in_the_jsons_order_named_by_sha256():
    doc = _parse(436535)
    obj = json.loads((CORPUS / "objects/0436/436535/object.json").read_bytes())
    names = [n for _u, n in acquire_met.image_names(obj)]
    assert len(names) == 1 + len(obj["additionalImages"]) == 5
    assert [f.href for f in doc.figures] == names
    assert [f.seq for f in doc.figures] == list(range(5))
    for f in doc.figures:
        data = (CORPUS / "objects/0436/436535" / f.href).read_bytes()
        assert f.sha256 == hashlib.sha256(data).hexdigest() and f.bytes == len(data)
        assert f.media_type == "image/jpeg" and f.carried and f.caption == ""
    got = SOURCE.read_figures(Path(_docs()[436535].locator), 436535, {f.sha256: f.href for f in doc.figures})
    assert set(got) == {f.sha256 for f in doc.figures}


def test_not_public_domain_object_has_no_images_and_keeps_its_rights():
    doc = _parse(1)
    assert doc.figures == [] and doc.extra["isPublicDomain"] is False
    assert doc.extra["primaryImage"] == "" and doc.extra["additionalImages"] == []
    assert "measurements" in doc.extra and "rightsAndReproduction" in doc.extra
    assert doc.title == "One-dollar Liberty Head Coin"


def test_parse_is_deterministic():
    a, b = _parse(45734), _parse(45734)
    assert a == b


def test_an_image_the_json_names_and_the_unit_lacks_is_refused(tmp_path):
    shutil.copytree(CORPUS, tmp_path / "c")
    (tmp_path / "c/objects/0045/45734/DP251120.jpg").unlink()
    from app.plugins.base import UnindexableDocument
    with pytest.raises(UnindexableDocument, match="DP251120.jpg"):
        _parse(45734, tmp_path / "c")


def test_a_key_the_api_adds_is_kept_and_said(tmp_path):
    shutil.copytree(CORPUS, tmp_path / "c")
    p = tmp_path / "c/objects/0000/1/object.json"
    obj = json.loads(p.read_bytes())
    obj["newField"] = "x"
    p.write_text(json.dumps(obj))
    doc = _parse(1, tmp_path / "c")
    assert doc.extra["unlisted"] == {"newField": "x"}
    assert [i.code for i in doc.issues] == ["unlisted-field"]


def test_a_bce_date_is_no_yyyy():
    assert met_source.date_of({"objectBeginDate": -1000}) is None
    assert met_source.date_of({"objectBeginDate": 33}) == "0033"


def test_image_names_are_unique_within_an_object():
    obj = {"primaryImage": "https://images.metmuseum.org/a/original/x.jpg",
           "additionalImages": ["https://images.metmuseum.org/b/original/x.jpg",
                                "https://images.metmuseum.org/a/original/x.jpg", ""]}
    assert [n for _u, n in acquire_met.image_names(obj)] == ["x.jpg", "2-x.jpg"]


# -- the acquisition ---------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self.content = status, json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.content)


class _Client:
    def __init__(self, answers):
        self.answers, self.urls = list(answers), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        self.urls.append(url)
        return self.answers.pop(0)


def test_listing_names_the_csv_first_then_every_object(monkeypatch):
    drawn = []
    monkeypatch.setattr(acquire_met.budget, "acquire_bytes", drawn.append)
    client = _Client([_Resp(503, {}), _Resp(200, {"total": 3, "objectIDs": [436535, 1, 45734]})])
    listing = acquire_met.MetListing(client_factory=lambda: client)
    ep = Endpoint(transport=Transport.HTTPS, url=acquire_met.API_URL)
    got = list(listing.list(ep, ACQUIRE_REGISTRY["met"], None))
    assert got[0].rel_path == "MetObjects.csv"
    assert [r.rel_path for r in got[1:]] == ["objects/0000/1/object.json", "objects/0045/45734/object.json",
                                             "objects/0436/436535/object.json"]
    assert sum(drawn) == sum(len(r.content) for r in [_Resp(503, {}), _Resp(200, {"total": 3, "objectIDs": [436535, 1, 45734]})])


def test_listing_never_truncates(monkeypatch):
    monkeypatch.setattr(acquire_met.budget, "acquire_bytes", lambda n: None)
    client = _Client([_Resp(200, {"total": 4, "objectIDs": [1, 2]})])
    with pytest.raises(TransportError, match="states 4"):
        list(acquire_met.MetListing(client_factory=lambda: client).list(
            Endpoint(transport=Transport.HTTPS, url=acquire_met.API_URL), ACQUIRE_REGISTRY["met"], None))


class _Transport:
    """Serves the fixture's bytes by URL path, as HttpsTransport.fetch_one would."""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def fetch_one(self, endpoint, remote, dest, resume_from):
        url = f"{endpoint.url}/{remote.remote}"
        self.calls.append(url)
        if self.fail_on and self.fail_on in url:
            raise TransportError(f"{url}: ReadTimeout")
        if url.startswith(acquire_met.API_URL):
            oid = int(url.rsplit("/", 1)[1])
            data = (CORPUS / acquire_met.unit_path(oid)).read_bytes()
        else:
            data = next(CORPUS.glob(f"objects/*/*/{Path(remote.remote).name}")).read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)


def test_a_unit_lands_its_images_then_its_json_last(tmp_path):
    t = _Transport()
    units = acquire_met.MetUnits(transport=t)
    ep = ACQUIRE_REGISTRY["met"].endpoints[0]
    remote = RemoteFile(rel_path=acquire_met.unit_path(436535), remote="public/collection/v1/objects/436535")
    assert units.handles(remote)
    dest = tmp_path / remote.rel_path
    moved = units.fetch(ep, remote, dest)
    unit = CORPUS / "objects/0436/436535"
    assert sorted(p.name for p in dest.parent.iterdir()) == sorted(p.name for p in unit.iterdir())
    assert moved == sum(p.stat().st_size for p in unit.iterdir())
    assert t.calls[0].startswith(acquire_met.API_URL) and all("images.metmuseum.org" in c for c in t.calls[1:])
    assert dest.read_bytes() == (unit / "object.json").read_bytes()


def test_a_stopped_unit_resumes_without_its_json_and_without_refetching(tmp_path):
    ep = ACQUIRE_REGISTRY["met"].endpoints[0]
    remote = RemoteFile(rel_path=acquire_met.unit_path(45734), remote="public/collection/v1/objects/45734")
    dest = tmp_path / remote.rel_path
    with pytest.raises(TransportError):
        acquire_met.MetUnits(transport=_Transport(fail_on="DP251120")).fetch(ep, remote, dest)
    assert not dest.exists()                     # no completion mark: discover does not see it
    t = _Transport()
    acquire_met.MetUnits(transport=t).fetch(ep, remote, dest)
    assert dest.is_file()
    assert not any("DP251139" in c or "DP251138" in c for c in t.calls)   # landed before the stop


def test_the_csv_is_fetched_from_the_lfs_host(tmp_path):
    t = _Transport()

    def csv(endpoint, remote, dest, resume_from):
        t.calls.append(f"{endpoint.url}/{remote.remote}")
        dest.write_bytes(b"x")
        return 1
    t.fetch_one = csv
    units = acquire_met.MetUnits(transport=t)
    units.fetch(ACQUIRE_REGISTRY["met"].endpoints[0], RemoteFile(rel_path="MetObjects.csv"), tmp_path / "MetObjects.csv")
    assert t.calls == [f"{acquire_met.CSV_URL}/{acquire_met.CSV_REMOTE}"]
