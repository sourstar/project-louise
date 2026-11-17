"""Wikidata's completion check for the update by cadence
(app/plugins/wikidata/completion.py; docs/notes/collection-updates.md §6),
read from the origin's own files as the coordinator's probe found them
(2026-11-17). A run is complete when its md5sums list the full dump; a run
still being written is waited for, a 404 too; a run carrying no full dump
(lexemes only) is passed over for the one before it; and the fetched dump is
held to the sums' line, a mismatch failing the fetch, never the run."""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.hold import update
from app.plugins.wikidata import completion

NOW = datetime(2026, 12, 2, 3, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 12, 1, tzinfo=timezone.utc)
HOOK = completion.Md5Sums()


def sums(run: str, *names: str) -> bytes:
    return b"".join(f"{hashlib.md5(n.encode()).hexdigest()}  wikidata-{run}-{n}\n".encode() for n in names)


FULL = ("all.json.gz", "all.json.bz2", "all-BETA.nt.bz2", "all-BETA.ttl.bz2", "truthy-BETA.nt.bz2",
        "lexemes.json.bz2")


def _ctx(bodies: dict[str, bytes | None], asked: list):
    def fetch(spec, recs):
        asked.extend(r.rel_path for r in recs)
        return {r.rel_path: bodies.get(r.rel_path) for r in recs}
    return SimpleNamespace(hook=HOOK, fetch_records=fetch, spec=None, now=NOW, log=lambda m: None)


def test_a_run_whose_sums_list_the_dump_is_complete_and_one_not_listing_it_waits():
    rec = HOOK.records("20261109", ["family=person", "family=work"])
    assert [r.rel_path for r in rec] == ["wikidatawiki/entities/20261109/wikidata-20261109-md5sums.txt"]
    assert HOOK.verdict(rec[0], sums("20261109", *FULL)) is None
    assert "still being written" in HOOK.verdict(rec[0], sums("20261109", "all.json.gz"))
    assert "no md5sums yet" in HOOK.verdict(rec[0], None), "a 404 is waiting"
    assert "no finished file" in HOOK.verdict(rec[0], b"")


def test_the_run_the_cutoff_names_passes_over_a_run_without_the_full_dump():
    asked: list = []
    bodies = {completion.sums_path("20261127"): sums("20261127", "lexemes.json.bz2", "truthy-BETA.nt.bz2"),
              completion.sums_path("20261123"): sums("20261123", *FULL)}
    run, _recs = update.complete_run(_ctx(bodies, asked), ["20261201", "20261127", "20261123", "20261109"],
                                    CUTOFF, ["family=person"])
    assert run == "20261123", "the last run before the cutoff whose sums list all.json.bz2"
    assert asked == [completion.sums_path("20261127"), completion.sums_path("20261123")], \
        "the run of the cutoff's own day is after it; nothing older than the answer is asked"


@pytest.mark.parametrize("body, why", [
    (None, "no md5sums yet"),                                       # a 404: the run has begun, nothing done
    (sums("20261130", "all.json.gz"), "still being written"),     # the full dump half written
])
def test_a_run_still_being_written_is_waited_for_never_passed_over(body, why):
    asked: list = []
    bodies = {completion.sums_path("20261130"): body, completion.sums_path("20261123"): sums("20261123", *FULL)}
    with pytest.raises(update._Waiting, match=why):
        update.complete_run(_ctx(bodies, asked), ["20261130", "20261123"], CUTOFF, ["family=person"])
    assert asked == [completion.sums_path("20261130")]


def test_a_full_run_whose_bz2_never_comes_does_not_hold_the_update_for_ever():
    rec = HOOK.records("20261120", [])[0]
    half = sums("20261120", "all.json.gz", "all-BETA.nt.bz2")
    assert HOOK.skips(rec, half, now=datetime(2026, 11, 22, tzinfo=timezone.utc)) is None
    assert "never coming" in HOOK.skips(rec, half, now=NOW)


def test_no_run_carrying_the_dump_is_waited_for():
    bodies = {completion.sums_path(r): sums(r, "lexemes.json.bz2") for r in ("20261127", "20261125")}
    with pytest.raises(update._Waiting, match="carries the collection's files"):
        update.complete_run(_ctx(bodies, []), ["20261127", "20261125"], CUTOFF, ["family=person"])


def test_wikipedias_check_still_asks_the_newest_run_alone():
    """A hook without `skips` (wikipedia's dumpstatus) is unchanged: the last
    run before the cutoff, waited for while incomplete."""
    from app.plugins.wikipedia.completion import DumpStatus
    asked: list = []
    ctx = SimpleNamespace(hook=DumpStatus(), fetch_records=lambda spec, recs: asked.extend(recs) or {},
                          spec=None, now=NOW, log=lambda m: None)
    with pytest.raises(update._Waiting, match="has not begun"):
        update.complete_run(ctx, ["20261120", "20261101"], CUTOFF, ["lang=tr"])
    assert {r.rel_path for r in asked} == {"trwiki/20261120/dumpstatus.json"}


def _fetched(tmp_path, run: str, data: bytes, listed: bytes):
    d = tmp_path / completion.ENTITIES_DIR / run
    d.mkdir(parents=True)
    (d / completion.dump_name(run)).write_bytes(data)
    (d / f"wikidata-{run}-md5sums.txt").write_bytes(listed)
    return d / completion.dump_name(run)


def test_the_fetched_dump_is_held_to_the_origins_md5_and_a_mismatch_fails_the_fetch(tmp_path):
    data = b"[\n{}\n]\n"
    good = f"{hashlib.md5(data).hexdigest()}  {completion.dump_name('20261109')}\n".encode()
    dump = _fetched(tmp_path / "a", "20261109", data, good)
    assert HOOK.verify("20261109", [], tmp_path / "a") == [] and dump.is_file()
    bad = f"{'0' * 32}  {completion.dump_name('20261109')}\n".encode()
    dump = _fetched(tmp_path / "b", "20261109", data, bad)
    got = HOOK.verify("20261109", [], tmp_path / "b")
    assert len(got) == 1 and "the fetch is bad, not the run" in got[0] and "removed" in got[0]
    assert not dump.exists(), "removed, so the next fetch takes it again instead of adopting it"


def test_the_pass_counts_a_mismatch_as_a_failed_fetch(tmp_path, monkeypatch):
    from app import db
    from app.acquire import engine
    data = b"[\n{}\n]\n"
    _fetched(tmp_path, "20261109", data, f"{'0' * 32}  {completion.dump_name('20261109')}\n".encode())
    monkeypatch.setattr(update, "_at_run", lambda ctx, run: SimpleNamespace(profile="all", corpus_dir=tmp_path))
    monkeypatch.setattr(db, "connect", lambda c: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(engine, "run_acquisition", lambda *a, **k: {"fetched": 3, "total": 3})
    monkeypatch.setattr(engine, "plan_acquisition", lambda *a, **k: SimpleNamespace(
        files_pending=0, files_total=3, bytes_total=len(data)))
    ctx = SimpleNamespace(hook=HOOK, collection=SimpleNamespace(slices="all"), spec=SimpleNamespace(
        slice_profiles={"family=person": "person"}), stop=threading.Event(), root=tmp_path, name="wikidata",
        log=lambda m: None)
    with pytest.raises(update._Failed, match="the fetch is bad, not the run"):
        update._fetch(ctx, {"run": "20261109", "steps": {}}, "2026-12-02T03:00:00Z")
