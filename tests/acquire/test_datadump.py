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
    cellar.UNITS.refetch(_spec(mock), src.unit_rel("en", ACT), tmp_path / "again", parser=3)
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


def test_any_folder_name_zips_at_any_depth_and_other_folders_passed_over(mock, tmp_path):
    """München's layout: /mnt/tank/louise-measure/datadump-<l>/, beside other
    measurements' folders."""
    files = _formex(ACT, "en")
    mock.add(ACT, "en", ACT, uuid=UUID, manif_modified=OLD)
    root = tmp_path / "louise-measure"
    folder = root / "datadump-en"
    (folder / "fmx").mkdir(parents=True)
    with zipfile.ZipFile(folder / "fmx" / "EN_FMX_1.zip", "w") as z:
        for n, b in files.items():
            z.writestr(f"{UUID}/fmx4/{n}", b)
    (folder / datadump.STATEMENT).write_text(json.dumps({"language": "en", "created": CREATED}))
    (root / "speed-benchmark").mkdir()
    said: list[str] = []
    dumps = datadump.Dumps(root, log=said.append)
    got = cellar.fetch_unit(mock.base, ACT, "en", tmp_path / src.unit_rel("en", ACT), dump=dumps.for_language("en"))
    assert got.from_dump == sum(len(b) for b in files.values()) and not _item_gets(mock)
    assert not said, "a folder that is not a dump is not reported"


def test_a_dated_dump_a_level_down_is_read_and_each_language_takes_its_newest(tmp_path):
    """2026-11-26: a whole dump of 2026-11-15 (601 GB) sat as
    `dump-2026-11-15/datadump-<lang>/` beside the 11-08 cut's
    `datadump-<lang>/`; only the top level was read, and the bulk-first
    check never saw the newer dump. EuroVoc's folder beside them is no dump."""
    root = tmp_path / "louise-measure"
    _dump(root, "bg", {"x/fmx4/y": b"old"}, created="2026-11-08T00:00:00+14:00")
    dated = root / "dump-2026-11-15"
    _dump(dated, "bg", {"x/fmx4/y": b"new"}, created="2026-11-15T01:00:00+01:00")
    _dump(dated, "sv", {"x/fmx4/y": b"new"}, created="2026-11-15T01:00:00+01:00")
    (dated / "datadump-mtd").mkdir()
    with zipfile.ZipFile(dated / "datadump-mtd" / "LEG_MTD.zip", "w") as z:
        z.writestr("u/tree_non_inferred.rdf", b"<rdf/>")
    (root / "euvoc-2026-11-16").mkdir()
    (root / "euvoc-2026-11-16" / "eurovoc.rdf").write_bytes(b"<rdf/>")
    said: list[str] = []
    found = datadump.Dumps(root, log=said.append)._scan()
    assert found["bg"][0] == dated / "EURLEX_BG" and found["bg"][1].date().isoformat() == "2026-11-15"
    assert found["sv"][0] == dated / "EURLEX_SV"
    assert any("the dump of 2026-11-15" in s and "not the older one" in s for s in said)
    assert not any("datadump-mtd" in s or "euvoc" in s for s in said), said
    from app.acquire import eurlex_dump
    assert not eurlex_dump.unread_statements(root, tmp_path / "corpus"), "nothing left unread"


def test_a_reference_source_beside_the_dumps_is_not_taken_for_a_dump(tmp_path):
    """2026-11-26: /datadump/euvoc-2026-11-16 (EuroVoc's release) was said
    every pass as "the newest Data Dump folder is not used"."""
    from app.acquire import datadump
    from app.refs import eurovoc
    ev = tmp_path / "euvoc-2026-11-16"
    ev.mkdir()
    (ev / eurovoc.FILES[0]).write_bytes(b"<rdf/>")
    import zipfile
    with zipfile.ZipFile(ev / "x.zip", "w") as z:
        z.writestr("a", b"a")
    assert ev not in datadump.dump_folders(tmp_path)


def test_a_dated_dump_holding_an_unpacked_eurovoc_beside_its_languages_is_still_the_dump(tmp_path):
    """Architecture's read of b4f6b52a: the EuroVoc test ran before the
    statements, over the whole tree, and hid the newer bulk."""
    import json
    from app.acquire import datadump
    from app.refs import eurovoc
    dated = tmp_path / "dump-2026-12-15"
    bg = dated / "datadump-bg"
    bg.mkdir(parents=True)
    (bg / "datadump.json").write_text(json.dumps({"language": "bg", "created": "2026-12-15T01:00:00+01:00"}))
    ev = dated / "euvoc-2026-12-01"
    ev.mkdir()
    (ev / eurovoc.FILES[0]).write_bytes(b"<rdf/>")
    assert datadump.dump_folders(tmp_path) == [bg]


def test_a_folder_holding_eurovoc_deep_below_is_not_taken_for_eurovoc(tmp_path):
    import zipfile
    from app.acquire import datadump
    from app.refs import eurovoc
    d = tmp_path / "datadump-it"
    (d / "deep").mkdir(parents=True)
    (d / "deep" / eurovoc.FILES[0]).write_bytes(b"<rdf/>")
    with zipfile.ZipFile(d / "x.zip", "w") as z:
        z.writestr("a", b"a")
    assert datadump.dump_folders(tmp_path) == [d], "said as before (no statement), never dropped"
