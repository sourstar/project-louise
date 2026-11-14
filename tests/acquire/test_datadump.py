"""The EUR-Lex Data Dump as a local transport for Cellar's items
(app/acquire/datadump.py): an item is read from the dump's zip only when the
notice lists it by that name and size, the member is the WORK's (never a
name alone), and its manifestation was last modified before the dump's
creation less the margin; everything else comes from Cellar, and
`records-vs-origin` never reads the dump. Against the local Cellar mock of
tests/acquire/test_cellar.py, never the real origin."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.acquire import cellar, datadump
from app.plugins.eurlex import source as src
from tests.acquire.test_cellar import ACT, Cellar, _formex, _spec

UUID = "ae929f3c-4a24-11e6-9c64-01aa75ed71a1"
OTHER = "0b7c1d2e-4a24-11e6-9c64-01aa75ed71a1"
OLD = "2016-07-01T10:00:00+02:00"          # before the dump
CREATED = "2026-11-08T01:00:00+01:00"


@pytest.fixture
def mock():
    m = Cellar()
    yield m
    m.server.shutdown()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(cellar.time, "sleep", lambda s: None)


def _dump(root: Path, lang: str, members: dict[str, bytes], created: str | None = CREATED) -> Path:
    folder = root / f"EURLEX_{lang.upper()}"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "fmx4.zip", "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    if created is not None:
        (folder / datadump.STATEMENT).write_text(json.dumps({"language": lang, "created": created}))
    return root


def _dumps(root: Path) -> datadump.Dumps:
    return datadump.Dumps(root, log=lambda *_: None)


def _item_gets(mock) -> list[str]:
    return [r[1] for r in mock.gets("/resource/cellar/")]


def test_an_item_the_dump_holds_for_the_work_is_read_from_it_and_the_unit_is_cellar_s(mock, tmp_path):
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=OLD)
    body = max((n for n in files if not n.endswith(".doc.xml")), key=lambda n: len(files[n]))  # its largest item
    dumps = _dumps(_dump(tmp_path / "dump", "en", {f"{UUID}/fmx4/{body}": files[body]}))
    dest = tmp_path / "corpus" / src.unit_rel("en", ACT)
    got = cellar.fetch_unit(mock.base, ACT, "en", dest, dump=dumps.for_language("en"))
    assert {n: (dest / n).read_bytes() for n in files} == files, "the same bytes as Cellar's"
    assert got.from_dump == len(files[body]) and got.moved > 0
    assert len(_item_gets(mock)) == len(files) - 1, "every item asked for but the one the dump gave"
    assert dumps.for_language("en").counts["taken"] == 1
    # and the unit is the one a fetch without the dump lands
    plain = tmp_path / "plain" / src.unit_rel("en", ACT)
    cellar.fetch_unit(mock.base, ACT, "en", plain)
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == {p.name: p.read_bytes() for p in plain.iterdir()}


def test_the_same_name_under_another_work_is_never_taken(mock, tmp_path):
    """31997R1466 and R1467: every act of one OJ issue ships a toc of the same name."""
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=OLD)
    dumps = _dumps(_dump(tmp_path / "dump", "en", {f"{OTHER}/fmx4/{n}": b for n, b in files.items()}))
    got = cellar.fetch_unit(mock.base, ACT, "en", tmp_path / src.unit_rel("en", ACT), dump=dumps.for_language("en"))
    assert got.from_dump == 0 and len(_item_gets(mock)) == len(files)
    assert dumps.for_language("en").counts["not-in-dump"] == len(files)


@pytest.mark.parametrize("modified, why", [("2026-11-07T12:00:00Z", "modified-since"), (None, "undated")])
def test_a_manifestation_modified_near_or_after_the_dump_or_undated_is_asked_for(mock, tmp_path, modified, why):
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=modified)
    dumps = _dumps(_dump(tmp_path / "dump", "en", {f"{UUID}/fmx4/{n}": b for n, b in files.items()}))
    got = cellar.fetch_unit(mock.base, ACT, "en", tmp_path / src.unit_rel("en", ACT), dump=dumps.for_language("en"))
    assert got.from_dump == 0 and len(_item_gets(mock)) == len(files)
    assert dumps.for_language("en").counts[why] == len(files)


def test_a_dump_member_of_another_size_than_the_notice_s_is_not_taken(mock, tmp_path):
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=OLD)
    dumps = _dumps(_dump(tmp_path / "dump", "en", {f"{UUID}/fmx4/{n}": b + b" " for n, b in files.items()}))
    dest = tmp_path / src.unit_rel("en", ACT)
    got = cellar.fetch_unit(mock.base, ACT, "en", dest, dump=dumps.for_language("en"))
    assert got.from_dump == 0 and all((dest / n).read_bytes() == b for n, b in files.items())
    assert dumps.for_language("en").counts["other-size"] == len(files)


def test_a_folder_without_its_statement_or_another_language_gives_nothing(mock, tmp_path):
    root = _dump(tmp_path / "dump", "en", {"x/fmx4/y": b"z"}, created=None)
    said: list[str] = []
    dumps = datadump.Dumps(root, log=said.append)
    assert dumps.for_language("en") is None and any(datadump.STATEMENT in s for s in said)
    assert _dumps(_dump(tmp_path / "d2", "fr", {"x/fmx4/y": b"z"})).for_language("en") is None
    assert datadump.Dumps(None).for_language("en") is None


def test_a_statement_without_a_zone_is_refused():
    assert datadump._stamp("2026-11-08T01:00:00") is None
    assert datadump._stamp("2026-11-08T01:00:00Z").utcoffset().total_seconds() == 0


def test_records_vs_origin_never_reads_the_dump(mock, tmp_path, monkeypatch):
    """The origin is Cellar: a refetch passes no dump, whatever the env says."""
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=OLD)
    _dump(tmp_path / "dump", "en", {f"{UUID}/fmx4/{n}": b for n, b in files.items()})
    monkeypatch.setenv(datadump.ENV, str(tmp_path / "dump"))
    monkeypatch.setattr(datadump, "_DUMPS", None)
    cellar.UNITS.refetch(_spec(mock), src.unit_rel("en", ACT), tmp_path / "again")
    assert len(_item_gets(mock)) == len(files)


def test_the_unit_fetcher_reads_the_dump_the_env_names(mock, tmp_path, monkeypatch):
    from app.acquire.spec import RemoteFile
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=OLD)
    _dump(tmp_path / "dump", "en", {f"{UUID}/fmx4/{n}": b for n, b in files.items()})
    monkeypatch.setenv(datadump.ENV, str(tmp_path / "dump"))
    monkeypatch.setattr(datadump, "_DUMPS", None)
    remote = RemoteFile(rel_path=cellar.rel_path("en", ACT), remote=cellar.encode(ACT, "en"))
    dest = tmp_path / "corpus" / remote.rel_path
    cellar.UNITS.fetch(_spec(mock).endpoints[0], remote, dest)
    assert not _item_gets(mock) and all((dest.parent / n).read_bytes() == b for n, b in files.items())
    monkeypatch.setattr(datadump, "_DUMPS", None)
