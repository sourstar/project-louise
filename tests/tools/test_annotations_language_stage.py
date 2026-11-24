"""tools/annotations_language_stage.py — the matcher's language-stage claims
merged into gutenberg-book-editions' claims file: every existing line kept as
it was but the matcher's own earlier stage claims, of any version; a person's
and Wikidata's stage claims kept beside; and the pass over the target resumes
at the first part not scanned, a part whose records changed (or a new matcher
version) scanned again."""
import json

from app import historical
from app.hold import codex as cx
from tools import annotations_language_stage as ls


def stage(subject, kind="matcher", version=1, **over):
    m = ({"kind": "matcher", "id": historical.MATCHER["id"], "version": version} if kind == "matcher"
         else {"kind": kind})
    c = {"type": "language-stage", "subject": subject, "object": "wikidata:Q135913388", "method": m,
         "contributor": "x"}
    c.update(over)
    return c


SAME = {"type": "same-work", "subject": "gutenberg:1", "object": "gutenberg:2",
        "method": {"kind": "wikidata"}, "contributor": "x"}


def test_the_merge_keeps_every_line_but_the_matchers_own_earlier_stage_claims():
    existing = [json.dumps(SAME).encode(), cx.canonical_json(stage("gutenberg:5", version=0)),
                json.dumps(stage("gutenberg:7", kind="human", object=None)).encode(),
                json.dumps(stage("gutenberg:8", kind="wikidata")).encode()]
    new = [stage("gutenberg:9"), stage("gutenberg:3")]
    lines, summary = ls.merge(existing, new)
    assert lines[:3] == [existing[0], existing[2], existing[3]], "kept as they were, byte for byte"
    assert [json.loads(x)["subject"] for x in lines[3:]] == ["gutenberg:3", "gutenberg:9"], "by subject"
    assert summary == {"kept": 3, "replaced": 1, "language_stage": 2, "claims": 5}


def test_the_pass_resumes_and_scans_a_changed_part_again(tmp_path, monkeypatch):
    parts = [("p1", "sha256:aa"), ("p2", "sha256:bb")]
    monkeypatch.setattr(ls, "_parts", lambda root, target: list(parts))
    scanned = []

    def each_part(fn, tasks, **_kw):
        for path, _contributor in tasks:
            scanned.append(path)
            yield [stage(f"gutenberg:{path}")]
    from app import parallel
    monkeypatch.setattr(parallel, "each_part", each_part)
    work = tmp_path / "work"
    first = ls.claims_of_target(tmp_path, "gutenberg", work, "x", log=lambda *_: None)
    assert scanned == ["p1", "p2"] and [c["subject"] for c in first] == ["gutenberg:p1", "gutenberg:p2"]
    again = ls.claims_of_target(tmp_path, "gutenberg", work, "x", log=lambda *_: None)
    assert scanned == ["p1", "p2"] and again == first, "nothing scanned twice"
    parts[1] = ("p2", "sha256:cc")                   # p2's records changed
    ls.claims_of_target(tmp_path, "gutenberg", work, "x", log=lambda *_: None)
    assert scanned == ["p1", "p2", "p2"]
    monkeypatch.setattr(ls, "historical_version", lambda: historical.MATCHER["version"] + 1)
    ls.claims_of_target(tmp_path, "gutenberg", work, "x", log=lambda *_: None)
    assert scanned[3:] == ["p1", "p2"], "a new matcher version scans every part again"
