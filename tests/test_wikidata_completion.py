"""Wikidata's completion check for the update by cadence
(app/plugins/wikidata/completion.py; docs/notes/collection-updates.md §6),
read from the origin's own files as the coordinator's probe found them
(2026-11-17), and the CURRENT RUN the update takes (app/hold/update.py
`complete_run`; operator, 2026-11-17: "if the current dump fails, don't use
an old dump (that should have already been integrated thus it's pointless);
retry gently"). A run is complete when its md5sums list the full dump; the
current run still being written is WAITED for however long it takes, never
passed over for an older one; a newer run complete first supersedes it; a
run carrying no full dump (lexemes only) is not a candidate; and the
fetched dump is held to the sums' line."""
from __future__ import annotations

import hashlib
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


def _ctx(bodies: dict[str, bytes | None], asked: list, now=NOW):
    def fetch(spec, recs):
        asked.extend(r.rel_path for r in recs)
        return {r.rel_path: bodies.get(r.rel_path) for r in recs}
    return SimpleNamespace(hook=HOOK, fetch_records=fetch, spec=None, now=now, log=lambda m: None)


def test_a_run_whose_sums_list_the_dump_is_complete_and_one_not_listing_it_waits():
    rec = HOOK.records("20261109", ["family=person", "family=work"])
    assert [r.rel_path for r in rec] == ["wikidatawiki/entities/20261109/wikidata-20261109-md5sums.txt"]
    assert HOOK.verdict(rec[0], sums("20261109", *FULL)) is None
    assert HOOK.verdict(rec[0], sums("20261109", "all.json.gz")) == ".json.gz done, .bz2 not yet"
    assert "no file finished yet" in HOOK.verdict(rec[0], None), "a 404 is waiting"
    assert "no file finished yet" in HOOK.verdict(rec[0], b"")


def test_the_current_run_passes_over_a_run_without_the_full_dump():
    asked: list = []
    bodies = {completion.sums_path("20261127"): sums("20261127", "lexemes.json.bz2", "truthy-BETA.nt.bz2"),
              completion.sums_path("20261123"): sums("20261123", *FULL)}
    run, _recs = update.complete_run(_ctx(bodies, asked), ["20261201", "20261127", "20261123", "20261109"],
                                    CUTOFF, ["family=person"], held="20261109")
    assert run == "20261123", "the newest run before the cutoff that carries the dump: lexemes-only is none"
    assert asked == [completion.sums_path("20261127"), completion.sums_path("20261123")], \
        "the current run complete: taken, and nothing newer or older is asked"


@pytest.mark.parametrize("body, why", [
    (None, "no file finished yet"),                                 # a 404: the run has begun, nothing done
    (sums("20261130", "all.json.gz"), ".json.gz done, .bz2 not yet"),   # the full dump half written
])
def test_the_current_run_still_being_written_is_waited_for_never_passed_over(body, why):
    asked: list = []
    bodies = {completion.sums_path("20261130"): body, completion.sums_path("20261123"): sums("20261123", *FULL)}
    with pytest.raises(update._Waiting) as e:
        update.complete_run(_ctx(bodies, asked), ["20261130", "20261123"], CUTOFF, ["family=person"])
    assert str(e.value).startswith(f"waiting for Wikimedia's 20261130 full dump: {why}")
    assert str(e.value).endswith("; asked again tonight")
    assert asked == [completion.sums_path("20261130")], "the complete older run is never asked: never taken"


def test_a_full_run_whose_bz2_is_late_holds_the_update_however_long_it_takes():
    """The 3-day pass-over is retired: a run listing all.json.gz is the
    current run until its .bz2 lands, or a newer run completes first."""
    rec = HOOK.records("20261130", [])[0]
    half = sums("20261130", "all.json.gz", "all-BETA.nt.bz2")
    assert HOOK.skips(rec, half, now=datetime(2027, 3, 1, tzinfo=timezone.utc)) is None
    asked: list = []
    bodies = {completion.sums_path("20261130"): half, completion.sums_path("20261123"): sums("20261123", *FULL)}
    for night in range(2, 30, 7):                         # four weeks of nights
        with pytest.raises(update._Waiting, match="20261130 full dump: .json.gz done, .bz2 not yet"):
            update.complete_run(_ctx(bodies, asked, now=NOW.replace(day=night)), ["20261130", "20261123"],
                                CUTOFF, ["family=person"], held="20261102")
    assert completion.sums_path("20261123") not in asked


def test_a_newer_run_complete_first_supersedes_the_current_one():
    asked: list = []
    logged: list = []
    bodies = {completion.sums_path("20261130"): sums("20261130", "all.json.gz"),
              completion.sums_path("20261204"): sums("20261204", "lexemes.json.bz2"),
              completion.sums_path("20261207"): sums("20261207", *FULL),
              completion.sums_path("20261123"): sums("20261123", *FULL)}
    ctx = _ctx(bodies, asked)
    ctx.log = logged.append
    run, _ = update.complete_run(ctx, ["20261207", "20261204", "20261130", "20261123"], CUTOFF,
                                 ["family=person"], held="20261102")
    assert run == "20261207", "newer is never pointless"
    assert asked == [completion.sums_path(r) for r in ("20261130", "20261207")]
    assert any("supersedes 20261130" in m for m in logged), logged


def test_a_run_not_newer_than_the_one_the_collection_reads_is_never_taken():
    asked: list = []
    bodies = {completion.sums_path("20261130"): sums("20261130", *FULL)}
    with pytest.raises(update._Waiting, match="newer than 20261130, which the collection reads"):
        update.complete_run(_ctx(bodies, asked), ["20261130", "20261123"], CUTOFF, ["family=person"],
                            held="20261130")
    assert asked == [], "an integrated run is not even asked"


def test_no_run_carrying_the_dump_is_waited_for():
    bodies = {completion.sums_path(r): sums(r, "lexemes.json.bz2") for r in ("20261127", "20261125")}
    with pytest.raises(update._Waiting, match="carries the collection's files"):
        update.complete_run(_ctx(bodies, []), ["20261127", "20261125"], CUTOFF, ["family=person"])


def test_wikipedias_check_asks_the_newest_run_and_never_an_older_one():
    """A hook without `skips` (wikipedia's dumpstatus): the last run before
    the cutoff is the current one, waited for while incomplete; a newer run,
    listed and incomplete, is asked too; the older run never."""
    from app.plugins.wikipedia.completion import DumpStatus
    asked: list = []
    ctx = SimpleNamespace(hook=DumpStatus(), fetch_records=lambda spec, recs: asked.extend(recs) or {},
                          spec=None, now=NOW, log=lambda m: None)
    with pytest.raises(update._Waiting, match="waiting for Wikimedia's 20261120 run: trwiki: the run has not begun"):
        update.complete_run(ctx, ["20261201", "20261120", "20261101"], CUTOFF, ["lang=tr"], held="20261020")
    assert [r.rel_path for r in asked] == ["trwiki/20261120/dumpstatus.json", "trwiki/20261201/dumpstatus.json"]


def _fetched(tmp_path, run: str, data: bytes, listed: bytes | None):
    d = tmp_path / completion.ENTITIES_DIR / run
    d.mkdir(parents=True)
    (d / completion.dump_name(run)).write_bytes(data)
    if listed is not None:
        (d / f"wikidata-{run}-md5sums.txt").write_bytes(listed)
    return d / completion.dump_name(run)


def test_the_fetched_dump_is_held_to_the_origins_md5_line(tmp_path):
    data = b"[\n{}\n]\n"
    md5 = hashlib.md5(data).hexdigest()
    _fetched(tmp_path / "a", "20261109", data, f"{md5}  {completion.dump_name('20261109')}\n".encode())
    (want,) = HOOK.expected("20261109", [], tmp_path / "a")
    assert (want.rel_path, want.md5, want.size) == (
        f"wikidatawiki/entities/20261109/{completion.dump_name('20261109')}", md5, None), \
        "the sums give no length: the origin's Range answer does"
    _fetched(tmp_path / "b", "20261109", data, b"")
    with pytest.raises(ValueError, match="lists no wikidata-20261109-all.json.bz2"):
        HOOK.expected("20261109", [], tmp_path / "b")
    _fetched(tmp_path / "c", "20261109", data, None)
    with pytest.raises(ValueError, match="not readable after the fetch"):
        HOOK.expected("20261109", [], tmp_path / "c")


def test_the_pass_mends_a_dump_off_its_md5_by_its_bad_block_never_fetching_it_whole(tmp_path, monkeypatch):
    """A mismatch is the FETCH failing, never the run — and it is mended:
    the dump is one bzip2 stream (lbzip2's, as the probe found), so its bad
    block's bytes alone are fetched again by Range, from the origin's own
    base with its User-Agent, after a one-byte Range for its length."""
    import bz2
    import dataclasses
    import threading

    from app import db
    from app.acquire import engine
    from app.acquire.spec import Endpoint, Transport
    from app.acquire.specs import WIKIDATA_SPEC
    from tests.acquire.mock_http_server import MockServer
    run = "20261109"
    good = bz2.compress(b"\n".join(b'{"id":"Q%d","type":"item"},' % i for i in range(150_000)), 9)
    origin = MockServer(tmp_path / "origin")
    try:
        rel = f"{completion.ENTITIES_DIR}/{run}/{completion.dump_name(run)}"
        (origin.root / rel).parent.mkdir(parents=True)
        (origin.root / rel).write_bytes(good)
        damaged = bytearray(good)
        damaged[len(good) // 2] ^= 0x10
        line = f"{hashlib.md5(good).hexdigest()}  {completion.dump_name(run)}\n".encode()
        dump = _fetched(tmp_path / "corpus", run, bytes(damaged), line)
        monkeypatch.setattr(update, "_at_run", lambda ctx, r: SimpleNamespace(profile="all",
                                                                              corpus_dir=tmp_path / "corpus"))
        monkeypatch.setattr(db, "connect", lambda c: SimpleNamespace(close=lambda: None,
                                                                     execute=lambda *a: None))
        monkeypatch.setattr(engine, "_require_license_ack", lambda spec, c: None)
        monkeypatch.setattr(engine, "run_acquisition", lambda *a, **k: pytest.fail("nothing is fetched whole"))
        monkeypatch.setattr(engine, "plan_acquisition", lambda *a, **k: SimpleNamespace(
            files_pending=0, files_total=3, bytes_total=len(good)))
        spec = dataclasses.replace(WIKIDATA_SPEC, endpoints=(
            Endpoint(transport=Transport.HTTPS, url=origin.base_url, max_conns=2),))
        ctx = SimpleNamespace(hook=HOOK, collection=SimpleNamespace(slices="all"), spec=spec,
                              stop=threading.Event(), root=tmp_path, name="wikidata", log=lambda m: None,
                              fetch_records=None)
        mk = update._fetch(ctx, {"run": run, "steps": {}, "step": "fetch"}, "2026-12-02T03:00:00Z")
        (m,) = mk["steps"]["fetch"]["counts"]["mended"]
        assert m["state"] == "mended" and 0 < m["refetched"] < len(good) / 2, m
        assert dump.read_bytes() == good
        asked = origin.range_requests[rel]
        assert asked[0] == "bytes=0-0" and len(asked) == 2, asked
        assert origin.last_user_agent[rel].startswith("louise-rag/")
    finally:
        origin.httpd.shutdown()
