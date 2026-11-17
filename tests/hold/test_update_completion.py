"""The completion check of the update by cadence (app/hold/update.py step
`due`): Wikimedia's own record that a run is complete, dumpstatus.json
(app/plugins/wikipedia/completion.py, the plugin's), and the origin asked
for it through the transfer budget with its etiquette
(app/acquire/completion.py, the platform's). No network: a fake client."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.acquire import completion
from app.plugins.wikipedia.completion import DumpStatus, Record

# ---------------------------------------------------------------- Wikimedia's record

def _status(**jobs) -> bytes:
    return json.dumps({"jobs": {k: {"status": v} for k, v in jobs.items()}}).encode()


def test_dumpstatus_is_read_per_held_wiki_for_the_two_jobs_the_collection_reads():
    ds = DumpStatus()
    recs = ds.records("20261101", ["lang=tr", "lang=en"])
    assert [(r.label, r.rel_path) for r in recs] == [("enwiki", "enwiki/20261101/dumpstatus.json"),
                                                     ("trwiki", "trwiki/20261101/dumpstatus.json")]
    r = recs[0]
    assert ds.verdict(r, _status(articlesmultistreamdump="done", pagepropstable="done",
                                 metahistorybz2dump="in-progress")) is None, \
        "a job the collection does not read does not hold it back"
    assert ds.verdict(r, _status(articlesmultistreamdump="done", pagepropstable="waiting")) == \
        "enwiki: pagepropstable waiting"
    assert ds.verdict(r, _status(articlesmultistreamdump="failed")) == \
        "enwiki: articlesmultistreamdump failed, pagepropstable not listed"
    assert ds.verdict(r, None) == "enwiki: the run has not begun (no dumpstatus.json)"
    assert "does not read" in ds.verdict(r, b"<html>")
    with pytest.raises(ValueError):
        ds.records("20261101", ["family=person"])


class _Client:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        self.asked.append((url, headers["User-Agent"]))
        a = self.answers[url.rsplit("/", 3)[-3]]
        if isinstance(a, Exception):
            raise a
        return httpx.Response(a[0], content=a[1], request=httpx.Request("GET", url))


def _spec(**etiquette):
    from app.acquire.spec import Endpoint, RateLimit, Transport
    return SimpleNamespace(source_id="wikipedia", etiquette=RateLimit(**etiquette),
                           endpoints=(Endpoint(transport=Transport.HTTPS, url="https://dumps.example/"),))


def test_the_origin_is_asked_through_the_budget_with_its_etiquette(monkeypatch):
    from app.acquire import budget
    drawn = []
    monkeypatch.setattr(budget, "acquire_bytes", drawn.append)
    body = _status(articlesmultistreamdump="done", pagepropstable="done")
    client = _Client({"enwiki": (200, body), "trwiki": (404, b"nope")})
    slept = []
    got = completion.records(_spec(min_interval_s=2.0),
                             DumpStatus().records("20261101", ["lang=en", "lang=tr"]),
                             client_factory=lambda: client, sleep=slept.append)
    assert got == {"enwiki/20261101/dumpstatus.json": body, "trwiki/20261101/dumpstatus.json": None}
    assert drawn == [len(body), 4], "invariant 5: every answer's bytes are drawn"
    assert all(ua.startswith("louise-rag/") for _, ua in client.asked)
    assert [u for u, _ in client.asked] == ["https://dumps.example/enwiki/20261101/dumpstatus.json",
                                            "https://dumps.example/trwiki/20261101/dumpstatus.json"]
    assert len(slept) == 1 and slept[0] > 1.5, "one request at a time, min_interval_s apart"


@pytest.mark.parametrize("answer", [(503, b""), (429, b""), httpx.ConnectTimeout("slow")])
def test_an_origin_away_is_a_deferral(monkeypatch, answer):
    from app.acquire import budget
    monkeypatch.setattr(budget, "acquire_bytes", lambda n: None)
    with pytest.raises(completion.OriginAway):
        completion.records(_spec(), [Record("lang=en", "enwiki", "enwiki/20261101/dumpstatus.json")],
                           client_factory=lambda: _Client({"enwiki": answer}))


def test_an_origin_that_refuses_is_a_failure_not_a_deferral(monkeypatch):
    from app.acquire import budget
    monkeypatch.setattr(budget, "acquire_bytes", lambda n: None)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        completion.records(_spec(), [Record("lang=en", "enwiki", "enwiki/20261101/dumpstatus.json")],
                           client_factory=lambda: _Client({"enwiki": (403, b"")}))


