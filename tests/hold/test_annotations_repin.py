"""`create annotations --repin` (2026-11-18): a target re-created at a new
parser — gutenberg 10 -> 12 — moves some records' text and its snapshot, and
its annotations collection, created again from `hold annotations claims`, was
refused line by line ("the claim was checked against other text", "checked
against 2026-08"). With `--repin` the claims' own pins are dropped and every
claim is pinned anew against the target, as `update` re-checks the claims it
holds; the dry run says first what moved, by method. And the book-editions
case: the predecessor's claims with the language-stage matcher's merged in
make a version-9 collection (09 §9.3's graph version)."""
import json

import pytest

from app import louise
from app.hold import annotations as ann
from app.hold import annotations_export
from app.hold import codex as cx
from app.hold import collection as col
from app.pack import records as pack_records
from tests.hold.conftest import KEY, NOW, PLATFORM
from tests.hold.test_annotations import a_target, make
from tests.hold.test_annotations_update import target_next

STAGE = {"type": "language-stage", "subject": "pubmed:4", "object": "wikidata:Q135913388",
         "method": {"kind": "matcher", "id": "historical-markers", "version": 1},
         "evidence": {"markers": 9, "words": 900, "why": "9 medieval markers in 900 words"},
         "confidence": "0.8", "contributor": "a test annotator"}


def claims_of(root, name, snap):
    d = col.shard_dir_of(root, name)
    return {(c["subject"], c["object"]): c for s in snap.shards
            for c in (ann.claim_of(r) for r in pack_records.iter_records(d / s.path))}


@pytest.fixture
def moved(tmp_path):
    """pubmed-same-work's claims (1,2) and (3,1) exported as it holds them,
    pinned to pubmed 2026-08; then pubmed's next snapshot, 2026-09, where
    record 2's text moved."""
    target, hashes = a_target(tmp_path)
    make(tmp_path)
    out = tmp_path / "claims.jsonl"
    annotations_export.export(tmp_path, "pubmed-same-work", out, log=lambda *_: None)
    now = target_next(tmp_path, target, {"pubmed:2": "two, revised"})
    assert now["pubmed:2"] != hashes["pubmed:2"] and now["pubmed:1"] == hashes["pubmed:1"]
    return tmp_path, out, now


def again(root, out, name="pubmed-same-work.next", **kw):
    kw.setdefault("log", lambda m: None)
    kw.setdefault("nonce", "0e" * 16)
    return ann.create(root, name, targets=[("pubmed", "2026-09")], claims=out, platform_image=PLATFORM,
                      snapshot_id="2026-10", key=KEY, now=NOW, **kw)


def test_without_repin_a_claim_on_moved_text_is_refused_and_nothing_written(moved):
    root, out, _now = moved
    with pytest.raises(ann.AnnotationsError, match="checked against"):
        again(root, out)
    assert not (root / "pubmed-same-work.next").exists()


def test_the_dry_run_says_what_the_repin_moves_by_method_and_writes_nothing(moved):
    root, out, _now = moved
    got = ann.preview(root, targets=[("pubmed", "2026-09")], claims=out, repin=True)
    assert got["repinned"] == {"snapshot only": 1, "text moved: matcher": 1}, \
        "(1,2) rests on record 2's moved text; (3,1) only on another snapshot of the same text"
    assert got["written"] is False and not (root / "pubmed-same-work.next").exists()
    assert "repinned" not in ann.preview(root, targets=[("pubmed", "2026-08")], claims=out), \
        "said only where asked"


def test_with_repin_every_claim_is_pinned_anew_and_the_two_paths_write_the_same(moved):
    root, out, now = moved
    lines = []
    codex, snap = again(root, out, repin=True, log=lines.append)
    held = claims_of(root, "pubmed-same-work.next", snap)
    assert held[("pubmed:1", "pubmed:2")]["object_hash"] == now["pubmed:2"], "pinned to the text held now"
    assert {c["target_snapshot"] for c in held.values()} == {"2026-09"}
    assert any("text moved: matcher 1" in m and "snapshot only 1" in m for m in lines), lines
    assert codex.version == cx.ANNOTATIONS_VERSION, "the same claim types: the version does not move"
    _c2, snap2 = again(root, out.read_bytes(), name="pubmed-same-work.mem", repin=True, _in_memory=True,
                       nonce="0d" * 16)
    assert claims_of(root, "pubmed-same-work.mem", snap2) == held, "the in-memory proof pins the same"


def test_the_predecessors_claims_with_stage_claims_make_a_graph_version_collection(moved):
    root, out, _now = moved
    merged = root / "merged.jsonl"
    merged.write_bytes(out.read_bytes() + json.dumps(STAGE).encode() + b"\n")
    codex, snap = again(root, merged, repin=True)
    assert codex.version == cx.GRAPH_VERSION, "a language-stage claim needs 09 §9.3's graph version"
    held = claims_of(root, "pubmed-same-work.next", snap)
    assert [k for k, c in held.items() if c["type"] == "same-work"] == [("pubmed:1", "pubmed:2"),
                                                                      ("pubmed:3", "pubmed:1")]
    stage = held[("pubmed:4", "wikidata:Q135913388")]
    assert stage["object_hash"] is None and stage["subject_hash"] is not None


def test_the_command_takes_repin(moved, monkeypatch, capsys):
    root, out, _now = moved
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    rc = louise.main(["hold", "collection", "create", "annotations", "--root", str(root), "--claims", str(out),
                      "--name", "pubmed-same-work.next", "--annotates", "pubmed", "--from-snapshot", "2026-09",
                      "--repin", "--dry-run"])
    assert rc == 0
    got = json.loads(capsys.readouterr().out)
    assert got["repinned"] == {"snapshot only": 1, "text moved: matcher": 1} and got["written"] is False
