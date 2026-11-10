"""tests/hold/test_annotations_stream.py — `create annotations` STREAMS its
claims file (operator, 2026-11-10; docs/notes/wikipedia-one-version.md
"Measured: the size": 12.3 M claims about 16.8 M of wikipedia's 52.9 M
records; app/hold/annotations_stream.py): for every mix of claims it writes
the tree the in-memory path wrote byte for byte — parts, relations, snapshot,
signature, Codex, origin, collection file — and refuses every bad file with
the same words; and its peak memory does not follow the claims held."""
import json
import random
import tracemalloc
from pathlib import Path

import pytest

from app.hold import annotations as ann, annotations_stream as st, collection as col, records as rec, \
    shard as shard_mod, snapshot as sn
from app.hold.codex import CodexError, License
from app.pack import records as pack_records
from tests.hold.conftest import KEY, NOW, PLATFORM, pubmed
from tests.hold.test_annotations import (TARGET_SNAPSHOT, a_chunk, a_claim, a_community, a_mention,
                                         a_relation, a_target, claims_file, two_targets)

STAR = "a star of versions"


def a_record(i: int, *, content_hash: str | None = None) -> dict:
    sections = [["Abstract", f"text {i}"]]
    return {"uid": f"pubmed:{i}", "native_id": str(i), "lang": "en", "title": f"T{i}", "creators": [],
            "date": "2023", "subjects": [], "keys": [], "sections": sections, "extra": {},
            "origin": f"x.xml#{i}",
            "content_hash": content_hash or rec.content_hash(f"T{i}", [tuple(s) for s in sections]),
            "origin_hash": "ab" * 16}


def a_wide_target(root: Path, n: int, per_part: int = 500, name: str = "pubmed", odd: int | None = None):
    """A held target of `n` records in two slices whose id ranges interleave
    — odd ids in one, even in the other — `per_part` records a part, so every
    uid lies in the range of a part of each slice, as on wikipedia, where a
    uid lies in ~170 parts' ranges and is held by one. Record `odd` carries
    a content_hash that is not lowercase hex, kept as the target gave it."""
    target = col.create(root, pubmed(license=License("green", None)), name=name)
    d = col.shard_dir_of(root, name)
    shards = []
    for sl, ids in (("year=2022", range(1, n + 1, 2)), ("year=2023", range(2, n + 1, 2))):
        ids = list(ids)
        for k, at in enumerate(range(0, len(ids), per_part), 1):
            records = [a_record(i, content_hash=("AB" * 16 if i == odd else None)) for i in ids[at:at + per_part]]
            p = d / shard_mod.shard_path(sl, k)
            pack_records.write_shard(p, records)
            raw = p.read_bytes()
            shards.append(sn.Shard(slice=sl, n=k, root=shard_mod.pieces_root(raw),
                                   records_root=rec.records_root(r["content_hash"] for r in records),
                                   first_id=records[0]["native_id"], last_id=records[-1]["native_id"],
                                   records=len(records), chunks=len(records), chunks_root="sha256:" + "22" * 32,
                                   tokens=0, bytes=len(raw)))
    snap = sn.Snapshot(collection_id=target.collection_id, snapshot_id=TARGET_SNAPSHOT, previous=None,
                       shards=tuple(shards), delta=sn.Delta(tuple(f"pubmed:{i}" for i in range(1, n + 1)), (), ()),
                       files={})
    (d / "snapshots").mkdir(exist_ok=True)
    (d / shard_mod.snapshot_path(TARGET_SNAPSHOT)).write_bytes(snap.canonical())
    return target


def star_claims(n: int, *, seed: int = 7, mess: bool = True) -> bytes:
    """wikipedia's shape (tools/annotations_from_wikipedia.py): records in
    groups of four, each other version `same-work` against the group's first,
    one JSON object a line with sorted keys — shuffled, so that the file's
    order is not the numbering's; with `mess`, CRLF ends, blank lines, a line
    whose evidence is not NFC and one with no newline at the end."""
    rnd = random.Random(seed)
    lines = []
    for i in range(1, n + 1):
        centre = i - (i - 1) % 4
        if i == centre:
            continue
        claim = {"type": "same-work", "subject": f"pubmed:{i}", "object": f"pubmed:{centre}",
                 "subject_lang": "it", "object_lang": "en", "method": {"kind": "wikidata"},
                 "evidence": {"wikidata": f"Q{centre}", "subject_tokens": i, "object_tokens": centre},
                 "confidence": "1", "contributor": STAR}
        if mess and i % 97 == 0:
            claim["evidence"]["label"] = "Pérez"           # NFD: the record carries it NFC
        lines.append(json.dumps(claim, ensure_ascii=False, sort_keys=True).encode())
    rnd.shuffle(lines)
    if not mess:
        return b"".join(x + b"\n" for x in lines)
    out = []
    for j, x in enumerate(lines):
        out.append(x + (b"\r\n" if j % 5 == 0 else b"\n"))
        if j % 11 == 0:
            out.append(b"  \n")
    return b"".join(out).rstrip(b"\n")


def tree(root: Path) -> dict[str, bytes | None]:
    """Every file under `root` with its bytes, every directory with None."""
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(Path(root).rglob("*"))}


def run(fn, root: Path, claims, **kw):
    kw.setdefault("target_ref", "pubmed")
    kw.setdefault("from_snapshot", TARGET_SNAPSHOT)
    if "targets" in kw:
        kw.pop("target_ref"), kw.pop("from_snapshot")
    return fn(root, kw.pop("name", "same"), claims=claims, platform_image=PLATFORM, snapshot_id="2026-09",
              key=KEY, now=NOW, nonce="0f" * 16, log=lambda m: None, **kw)


def identical(tmp_path, build, claims: bytes, *, monkeypatch=None, **kw):
    """The in-memory path, the stream from bytes and the stream from a file —
    the last with a sort that spills every few claims and small batches —
    each on its own copy of the same targets: the same trees."""
    roots = {k: tmp_path / k for k in ("memory", "bytes", "file")}
    for r in roots.values():
        r.mkdir()
        build(r)
    path = tmp_path / "claims.jsonl"
    path.write_bytes(claims)
    out = {"memory": run(ann._create_in_memory, roots["memory"], claims, **kw),
           "bytes": run(ann.create, roots["bytes"], claims, **kw)}
    if monkeypatch is not None:
        monkeypatch.setattr(st, "RUN_ITEMS", 37)
        monkeypatch.setattr(st, "BATCH", 50)
    out["file"] = run(ann.create, roots["file"], path, **kw)
    trees = {k: tree(r) for k, r in roots.items()}
    assert trees["bytes"] == trees["memory"], "streamed from bytes: the same tree"
    assert trees["file"] == trees["memory"], "streamed from a file, spilled: the same tree"
    assert path.read_bytes() == claims, "the file given is read, never moved"
    assert len({(c.collection_id, s.hash) for c, s in out.values()}) == 1
    return out["memory"], trees["memory"]


# ---- the same bytes ---------------------------------------------------------------------

def test_a_star_of_thousands_of_claims_writes_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "SHARD_RECORDS", 700)              # several parts: the Codex says so
    claims = star_claims(4000)
    (codex, snap), t = identical(tmp_path, lambda r: a_wide_target(r, 4000, per_part=300, odd=5), claims,
                                 monkeypatch=monkeypatch)
    assert snap.records == 3000 and len(snap.shards) == 5 and codex.slicing["shard_records"] == 700
    assert sum(1 for k in t if k.startswith("same/shard/relations/all/")) == 5
    d = Path(str(tmp_path / "memory")) / "same" / "shard"
    first = next(pack_records.iter_records(d / snap.shards[0].path))
    assert ann.claim_of(first)["subject"] == "pubmed:10", "numbered in byte order, not numeric"
    claims_by_subject = {ann.claim_of(r)["subject"]: ann.claim_of(r)
                         for s in snap.shards for r in pack_records.iter_records(d / s.path)}
    assert claims_by_subject["pubmed:6"]["object_hash"] == "AB" * 16, "a target's hash kept as it gave it"
    assert claims_by_subject["pubmed:194"]["evidence"]["label"] == "Pérez", "NFC"


@pytest.mark.parametrize("case", ["pairs", "directed", "stated"])
def test_the_2026_10_09_claims_write_the_same_tree(tmp_path, monkeypatch, case):
    a, b = a_claim(), a_claim("pubmed:3", "pubmed:1", confidence="0.73")
    files = {
        "pairs": claims_file(a, b),
        "directed": claims_file(a_claim(type="translation-of"), a_claim("pubmed:2", "pubmed:1", type="translation-of"),
                                a_claim("pubmed:4", "pubmed:3", method={"kind": "human"}, evidence={})),
        "stated": claims_file(a_claim(target_snapshot=TARGET_SNAPSHOT), b).replace(b"\n", b"\r\n\n"),
    }
    identical(tmp_path, a_target, files[case], monkeypatch=monkeypatch)


@pytest.mark.parametrize("container", ["jsonl", "parquet"])
def test_the_graph_claims_about_two_targets_write_the_same_tree(tmp_path, monkeypatch, container):
    probe = tmp_path / "probe"
    probe.mkdir()
    text, _, _ = two_targets(probe)
    claims = claims_file(a_mention(text), a_relation(text), a_community(), a_claim("pubmed:2", "pubmed:3"),
                         a_mention(text, start=14, end=19, surface="wrote"))
    identical(tmp_path, two_targets, claims, monkeypatch=monkeypatch, container=container,
              targets=[("pubmed", TARGET_SNAPSHOT), ("wikidata", "2026-07")], annotator="a graph builder")


def test_a_create_resumed_and_a_preview_say_what_the_in_memory_path_said(tmp_path):
    a_target(tmp_path)
    claims = claims_file(a_claim(), a_claim("pubmed:3", "pubmed:4", method={"kind": "wikidata"},
                                            evidence={"item": "Q1"}, confidence="0.96875"))
    codex, snap = run(ann.create, tmp_path, claims)
    before = tree(tmp_path)
    again = run(ann.create, tmp_path, claims)
    assert again[0] == codex and again[1].hash == snap.hash and tree(tmp_path) == before, "resumed: the same bytes"
    with pytest.raises(CodexError, match="different bytes"):
        run(ann.create, tmp_path, claims_file(a_claim()))
    assert (tmp_path / "same" / "origin" / "claims.jsonl").read_bytes() == claims, "a refusal moves nothing"
    assert not list(tmp_path.glob(".annotations-create*")), "no workspace left behind"
    got = ann.preview(tmp_path, target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT, claims=claims)
    ch = ann._checked(tmp_path, "pubmed", TARGET_SNAPSHOT, claims, annotator=None)
    assert (got["claims"], got["target_records_named"], got["by_type"], got["by_method"], got["version"]) == (
        len(ch.pinned), len({u for c in ch.pinned for u in ann.record_ends(c)}), {"same-work": 2},
        {"matcher:louise-classical-matcher": 1, "wikidata": 1}, 7)
    p = tmp_path / "c.jsonl"
    p.write_bytes(claims)
    assert ann.preview(tmp_path, target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT, claims=p) == got
    assert ann.targets_of_claims(tmp_path, p) == ann.targets_of_claims(tmp_path, claims) == [("pubmed", TARGET_SNAPSHOT)]


# ---- the same refusals -------------------------------------------------------------------

def _bad_files() -> dict[str, bytes]:
    c = a_claim
    bad_json = b'{"type": "same-work",\n'
    return {
        "empty": b"\n \r\n",
        "not json": claims_file(c()) + bad_json,
        "a repeat before a bad line": claims_file(c(), c("pubmed:2", "pubmed:1")) + bad_json,
        "a bad line before a repeat": claims_file(c(), c(confidence=0.5), c("pubmed:2", "pubmed:1")),
        "the first repeat in the file": claims_file(c("pubmed:3", "pubmed:4"), c(), c("pubmed:1", "pubmed:3"),
                                                    c("pubmed:4", "pubmed:3"), c("pubmed:2", "pubmed:1"),
                                                    c("pubmed:3", "pubmed:4")),
        "a check": claims_file(c(), c(type="related")),
        "ends not held": claims_file(*[c(f"pubmed:{90 + i}", "pubmed:1") for i in range(10)]),
        "a stated hash after an end not held": claims_file(c("pubmed:99", "pubmed:1"), c(subject_hash="00" * 16)),
        "a snapshot stated": claims_file(c(target_snapshot="2026-01")),
        "two contributors": claims_file(c(), c("pubmed:3", "pubmed:4", contributor="another")),
        "another target's uid": claims_file(c(obj="gutenberg:2")),
    }


@pytest.mark.parametrize("case", sorted(_bad_files()))
def test_a_bad_file_is_refused_with_the_in_memory_path_s_words_and_leaves_nothing(tmp_path, case):
    claims = _bad_files()[case]
    errors = []
    for k, fn in (("memory", ann._create_in_memory), ("stream", ann.create)):
        r = tmp_path / k
        r.mkdir()
        a_target(r)
        before = tree(r)
        with pytest.raises(Exception) as e:
            run(fn, r, claims)
        errors.append((type(e.value), str(e.value)))
        assert tree(r) == before, "refused before anything is written"
    assert errors[0] == errors[1]


def test_a_bad_graph_claim_is_refused_with_the_same_words(tmp_path):
    probe = tmp_path / "probe"
    probe.mkdir()
    text, _, _ = two_targets(probe)
    end_, _ = a_chunk(text, "pubmed:1", "Douglas Adams wrote it")
    for n, claims in enumerate((
            claims_file(a_mention(text, subject=dict(end_, text_hash="00" * 16))),
            claims_file(a_mention(text, surface="Douglas Adam")),
            claims_file(a_mention(text, object="wikidata:Q99"), a_community(subject_hash="00" * 16)),
            claims_file(a_mention(text, object="wikidata:Q99")))):
        errors = []
        for k, fn in (("memory", ann._create_in_memory), ("stream", ann.create)):
            r = tmp_path / f"{k}{n}"
            r.mkdir()
            two_targets(r)
            with pytest.raises(ann.AnnotationsError) as e:
                run(fn, r, claims, targets=[("pubmed", TARGET_SNAPSHOT), ("wikidata", "2026-07")])
            errors.append(str(e.value))
        assert errors[0] == errors[1]


# ---- the pieces --------------------------------------------------------------------------

def test_the_lines_are_read_claims_lines_and_read_back_by_offset(tmp_path):
    data = b'{"a":1}\r\n\n  \r\n{"b":2}\r\r\n{"c":3}'
    p = tmp_path / "c"
    p.write_bytes(data)
    want = [(i, raw[:-1] if raw.endswith(b"\r") else raw) for i, raw in enumerate(data.split(b"\n"), 1)]
    want = [(i, r) for i, r in want if r.strip()]
    for src in (st.ClaimsSource(data), st.ClaimsSource(p)):
        with src:
            got = list(src.lines())
            assert [(i, raw) for i, _, raw in got] == want
            assert [src.read(o, len(raw)) for _, o, raw in got] == [raw for _, raw in want]


def test_the_external_sort_spills_merges_and_keeps_equal_keys_in_order(tmp_path):
    rnd = random.Random(3)
    items = [((rnd.randrange(50).to_bytes(2, "big"), b"x"), k) for k in range(2000)]
    s = st.ExternalSort(tmp_path, run_items=97)
    for it in items:
        s.add(it)
    assert len(list(tmp_path.glob("run-*"))) == 2000 // 97
    assert list(s) == sorted(items) == sorted(items, key=lambda it: it[0])


def test_the_target_is_read_as_target_records_reads_it(tmp_path):
    target = a_wide_target(tmp_path, 3000, per_part=200, odd=8)
    t = ann.Target("pubmed", target, TARGET_SNAPSHOT, col.shard_dir_of(tmp_path, "pubmed"))
    rnd = random.Random(5)
    wanted = sorted({f"pubmed:{rnd.randrange(1, 3400)}" for _ in range(700)} | {"pubmed:8", "pubmed:x"})
    data = claims_file(*[a_claim(u, "pubmed:1") for u in wanted if u != "pubmed:1"])
    with st.ClaimsSource(data) as src:
        sv = st.survey(src, plugins=("pubmed",), orders={"pubmed": "numeric"})
    table = st.read_target(t, sv.ends["pubmed"])
    held = st.Held({"pubmed": table})
    held.load(wanted + ["pubmed:1"])
    expected = ann.target_records(t.shard_tree, target, TARGET_SNAPSHOT, wanted + ["pubmed:1"])
    assert {u: held.get(u)["content_hash"] for u in wanted + ["pubmed:1"] if held.get(u)} == \
        {u: r["content_hash"] for u, r in expected.items()}
    assert expected["pubmed:8"]["content_hash"] == "AB" * 16 and "pubmed:3399" not in expected
    assert table.held == len(expected)


# ---- on the pool --------------------------------------------------------------------------

def a_pool(monkeypatch):
    """Two spawned children and tasks of a few claims, so that every pass is
    cut into many tasks — a refusal in a late one — and each runs where a
    monkeypatch cannot reach (the cuts are made here)."""
    from app import config
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(st, "SURVEY_BYTES", 4000)
    monkeypatch.setattr(st, "PIN_CLAIMS", 97)
    monkeypatch.setattr(st, "RUN_ITEMS", 50)


def test_on_the_pool_a_star_writes_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "SHARD_RECORDS", 700)
    a_pool(monkeypatch)
    identical(tmp_path, lambda r: a_wide_target(r, 1600, per_part=300, odd=5), star_claims(1600))


def test_on_the_pool_graph_claims_re_cut_their_chunks_in_the_children(tmp_path, monkeypatch):
    """A chunk end is re-cut where the claim is pinned: in a child, from the
    record text the tables carry (`Held.save`), with the target's Codex."""
    probe = tmp_path / "probe"
    probe.mkdir()
    text, _, _ = two_targets(probe)
    claims = claims_file(a_mention(text), a_relation(text), a_community(), a_claim("pubmed:2", "pubmed:3"),
                         a_mention(text, start=14, end=19, surface="wrote"))
    monkeypatch.setattr(ann, "SHARD_RECORDS", 2)
    a_pool(monkeypatch)
    monkeypatch.setattr(st, "SURVEY_BYTES", 500)
    monkeypatch.setattr(st, "PIN_CLAIMS", 1)
    identical(tmp_path, two_targets, claims, targets=[("pubmed", TARGET_SNAPSHOT), ("wikidata", "2026-07")],
              annotator="a graph builder")


def _late(n: int = 1600) -> dict[str, bytes]:
    """Star claims with a refusal late in the file: in a later task than
    the first, after others the in-memory path would not have reached."""
    lines = star_claims(n, mess=False).splitlines()
    bad = json.loads(lines[1000])

    def file(edits: dict[int, bytes]) -> bytes:
        return b"".join(edits.get(j, x) + b"\n" for j, x in enumerate(lines))
    missing = {j: json.dumps(dict(json.loads(lines[j]), subject=f"pubmed:{90000 + j}")).encode()
               for j in (5, 300, 301, 650, 700, 710, 800, 801, 900, 1100)}
    return {
        "not json": file({1000: b'{"type": '}),
        "a repeat, then a bad line": file({600: lines[50], 1000: json.dumps(dict(bad, confidence=0.5)).encode()}),
        "the first repeat": file({1100: lines[3], 700: lines[650]}),
        "ends not held, across tasks": file(missing),
        "a hash after ends not held": file({**missing, 1001: json.dumps(dict(json.loads(lines[1001]),
                                                                             subject_hash="00" * 16)).encode()}),
    }


@pytest.mark.parametrize("case", sorted(_late()))
def test_on_the_pool_a_late_refusal_has_the_in_memory_path_s_words(tmp_path, monkeypatch, case):
    claims = _late()[case]
    errors = []
    for k, fn in (("memory", ann._create_in_memory), ("stream", ann.create)):
        if k == "stream":
            a_pool(monkeypatch)
        r = tmp_path / k
        r.mkdir()
        a_wide_target(r, 1600, per_part=300)
        with pytest.raises(Exception) as e:
            run(fn, r, claims)
        errors.append((type(e.value), str(e.value)))
        assert not (r / "same").exists() and not list(r.glob(".annotations-*"))
    assert errors[0] == errors[1]


# ---- the memory ---------------------------------------------------------------------------

def test_the_peak_does_not_follow_the_claims(tmp_path, monkeypatch):
    """200,000 claims about 266,667 records (MEASURED 2026-11-10, tracemalloc,
    in the commit that streamed `create`): the in-memory path peaked at 898 MB
    in its one process; the stream at 135 MB in one process — a part of
    50,000 records, the same at any count — and, on the pool, at 69 MB in
    this process while the children write the parts. Here: on the pool, this
    process under 96 MB — the claims' integers and digests (~100 bytes a
    claim), a sort run, the children's answers in flight."""
    n = 266_667
    a_wide_target(tmp_path, n, per_part=5000)
    path = tmp_path / "claims.jsonl"
    path.write_bytes(star_claims(n, mess=False))
    from app import config
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(st, "RUN_ITEMS", 1 << 16)
    tracemalloc.start()
    try:
        _codex, snap = run(ann.create, tmp_path, path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert snap.records == 200_000 and len(snap.shards) == 4
    assert peak < 96 << 20, f"peak {peak / 2**20:.0f} MB"
