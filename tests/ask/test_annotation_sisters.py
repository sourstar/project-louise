"""tests/ask/test_annotation_sisters.py — an annotation applies to every
held collection whose records match its claims, decided PER CLAIM BY HASH
(02 §2.10 and 05 §5.3, amended 2026-11-11, architecture session with the
operator): sisters holding the same records get every claim, a record that
moved in one sister is `stale` there and `current` in the other, a sister
cut by other layers has its mentions stale per chunk while the record-level
claims stand, a peer holding only the sister applies an annotation naming
the other, and the create's target inference counts collections of one
plugin with the same records as one target, refusing only where they differ.

`pubmed` stands for normattiva and `pubmed-nemo` for normattiva-nemo: the
fixtures (tests/hold/test_annotations.py) are pubmed's."""
import dataclasses
import shutil
from types import SimpleNamespace

import pytest

from app import collections as collections_mod, db
from app.ask import annotations as facts, works
from app.hold import annotations as ann, collection as col, records as rec
from app.hold.codex import Chunker, Layer
from tests.hold.conftest import pubmed
from tests.hold.test_annotations import (TARGET_SNAPSHOT, a_claim, a_mention, a_target, claims_file,
                                         make)

#: a sister: the same corpus, another space (02 §2.7) — another tolerance here
SISTER = {"space": dataclasses.replace(pubmed().space, tolerance="0.96")}
#: a sister whose layers differ: `fixed` windows of two tokens, not one chunk per abstract
OTHER_LAYERS = {"layers": (Layer(Chunker("fixed", 1, {"tokens": 2, "overlap": 0}), "default"),)}
TEXTS = ("Douglas Adams wrote it", "two", "three", "four")


def a_manifest(root, name, codex, hashes):
    """The collection as the search reads it: its manifest's documents."""
    c = collections_mod.Collection(
        name=name, plugin="pubmed", profile=None, embed_mode_default=None,
        corpus_dir=root / name / "shard", manifest_path=root / name / "state" / "manifest.sqlite",
        fts_dir=root / name / "index", lance_dir=root / name / "lance", codex=codex)
    with db.connect(c) as con:
        for uid, h in hashes.items():
            i = uid.split(":")[1]
            con.execute("INSERT INTO documents(uid, source, native_id, path, title, lang, content_hash) "
                        "VALUES(?,?,?,?,?,?,?)", (uid, "pubmed", i, f"x/{i}", f"T{i}", "en", h))
    return c


def held_annotations(root, codex):
    return SimpleNamespace(name="pubmed-same-work", codex=codex, plugin="annotations", license_ack=None,
                           manifest_path=root / "pubmed-same-work" / "state" / "manifest.sqlite")


def search(colls_and_snaps, held, root, uid="pubmed:1"):
    """One hit on `uid` per collection, each read from its snapshot; the
    states attached, by collection name."""
    lists, source_of, contexts = [], {}, {}
    for c, snap in colls_and_snaps:
        h = {"uid": uid, "rank": 1, "text": "…"}
        lists.append({"space_id": c.codex.space_id, "question": "relevance", "hits": [h]})
        source_of[id(h)] = c
        contexts[c.name] = {"collection_id": c.codex.collection_id, "snapshot_id": snap}
    assert facts.attach(lists, source_of, contexts, root=root, collections=[held]) == []
    return {c.name: {f["record"]["uid"]: f["state"] for f in lst["hits"][0]["annotations"]}
            for (c, _), lst in zip(colls_and_snaps, lists)}


def two_sisters(tmp_path, **sister):
    target, hashes = a_target(tmp_path, texts=TEXTS)
    nemo, nemo_hashes = a_target(tmp_path, name="pubmed-nemo", texts=TEXTS, **{**SISTER, **sister})
    assert nemo.collection_id != target.collection_id and nemo_hashes == hashes, "sisters: the same records"
    codex, _ = make(tmp_path, claims=claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1",
                                                                     type="translation-of")))
    held = held_annotations(tmp_path, codex)
    return (a_manifest(tmp_path, "pubmed", target, hashes), a_manifest(tmp_path, "pubmed-nemo", nemo, hashes),
            held, codex)


def test_sisters_holding_the_same_records_both_get_every_claim(tmp_path):
    tcoll, nemo, held, codex = two_sisters(tmp_path)
    assert [t.collection_id for t in ann.cx.targets_of(codex)] == [tcoll.codex.collection_id], \
        "annotates names the collection the claims were checked against, and only it"
    both = {"pubmed:2": "current", "pubmed:3": "current"}
    assert search([(tcoll, TARGET_SNAPSHOT), (nemo, TARGET_SNAPSHOT)], held, tmp_path) == {
        "pubmed": both, "pubmed-nemo": both}
    # the work groups follow the same rule: the sister's editions are one work
    work_of, members = works.work_groups(nemo, ["pubmed:1"], root=tmp_path, annotations=[held])
    assert work_of["pubmed:1"] == "pubmed:1" and {e.uid for e in members["pubmed:1"]} == {
        "pubmed:1", "pubmed:2", "pubmed:3"}


def test_a_record_changed_in_one_sister_is_stale_there_and_current_in_the_other(tmp_path):
    tcoll, nemo, held, _ = two_sisters(tmp_path)
    with db.connect(nemo) as con:
        con.execute("UPDATE documents SET content_hash=? WHERE uid='pubmed:2'", ("ff" * 16,))
    assert search([(tcoll, TARGET_SNAPSHOT), (nemo, TARGET_SNAPSHOT)], held, tmp_path) == {
        "pubmed": {"pubmed:2": "current", "pubmed:3": "current"},
        "pubmed-nemo": {"pubmed:2": "stale", "pubmed:3": "current"}}, \
        "per claim: the moved record's claim is stale in that sister only"


def test_a_sister_with_other_layers_has_its_mentions_stale_per_chunk_and_keeps_record_level_claims(tmp_path):
    tcoll, cut, held, _ = two_sisters(tmp_path, **OTHER_LAYERS)
    assert cut.codex.layers != tcoll.codex.layers
    assert search([(cut, TARGET_SNAPSHOT)], held, tmp_path) == {
        "pubmed-nemo": {"pubmed:2": "current", "pubmed:3": "current"}}, \
        "record-level claims: the records are the same, whatever the cut"
    mention = a_mention(tcoll.codex)
    hashes = {"pubmed:1": rec.content_hash("T1", [("Abstract", TEXTS[0])]), "wikidata:42": "aa" * 16}
    mention = {**mention, "subject_hash": hashes["pubmed:1"], "object_hash": hashes["wikidata:42"]}
    record = {"uid": "pubmed:1", "native_id": "1", "title": "T1", "sections": [["Abstract", TEXTS[0]]]}
    same = {"pubmed:1": ann.chunk_hashes(tcoll.codex, record)}
    other = {"pubmed:1": ann.chunk_hashes(cut.codex, record)}
    assert mention["subject"]["chunk_id"] in same["pubmed:1"]
    assert ann.state_of(mention, hashes, same) == "current", "the same layers: the chunk is there"
    assert ann.state_of(mention, hashes, other) == "stale", "other layers: the chunk is not there"
    assert ann.state_of(mention, hashes) == "current", "subject_hash still covers the record"
    assert ann.state_of(mention, hashes, {}) == "current", "a record the chunks do not cover: its hash"


def test_a_peer_holding_only_the_sister_applies_an_annotation_naming_the_other(tmp_path):
    tcoll, nemo, held, codex = two_sisters(tmp_path)
    shutil.rmtree(tmp_path / "pubmed")                # this peer never held normattiva's Codex
    with pytest.raises(Exception):
        col.resolve(tmp_path, "pubmed")
    assert search([(nemo, "2026-07")], held, tmp_path) == {
        "pubmed-nemo": {"pubmed:2": "current", "pubmed:3": "current"}}, \
        "another collection's cadence: the hash alone decides, whatever its snapshot id"
    assert [a.name for a in works._usable(nemo, [held], tmp_path)] == ["pubmed-same-work"]
    other = SimpleNamespace(name="x", codex=SimpleNamespace(annotates=SimpleNamespace(
        collection_id="sha256:" + "99" * 32)))
    assert not facts.is_about(tmp_path, other, (nemo.codex.collection_id,), {"pubmed"}), \
        "an annotations collection whose claims name no pubmed record: not consulted"


def test_inference_counts_one_plugin_s_same_records_as_one_target_and_refuses_where_they_differ(tmp_path):
    claims = claims_file(a_claim())
    a_target(tmp_path, texts=TEXTS)
    nemo, _ = a_target(tmp_path, name="pubmed-nemo", texts=TEXTS, snapshot_id="2026-09", **SISTER)
    said: list[str] = []
    assert ann.targets_of_claims(tmp_path, claims, log=said.append) == [("pubmed-nemo", "2026-09")], \
        "the newest snapshot"
    assert len(said) == 1 and said[0].startswith("the claims name pubmed records; pubmed-nemo (snapshot "
                                                 "2026-09) is the target") and "pubmed too" in said[0]
    assert ann.resolve_targets(tmp_path, claims) == [("pubmed-nemo", "2026-09")]
    # ties: the lowest collection_id
    tie = tmp_path / "tie"
    t1, _ = a_target(tie, texts=TEXTS)
    t2, _ = a_target(tie, name="pubmed-nemo", texts=TEXTS, **SISTER)
    lowest = min((t1.collection_id, "pubmed"), (t2.collection_id, "pubmed-nemo"))[1]
    assert ann.targets_of_claims(tie, claims) == [(lowest, TARGET_SNAPSHOT)]
    # records that differ: refused, both named — even where each holds the claims' records
    differ = tmp_path / "differ"
    a_target(differ, texts=TEXTS)
    a_target(differ, name="pubmed-too", texts=TEXTS[:3] + ("five",), snapshot_id="2026-09",
             cleaning_version=2)
    with pytest.raises(ann.AnnotationsError, match=r"records differ \(pubmed, pubmed-too: .*nothing is guessed"):
        ann.targets_of_claims(differ, claims)


def test_records_roots_name_what_a_snapshot_holds_whatever_its_slices_are_called(tmp_path):
    from app.share import seeder
    a_target(tmp_path, texts=TEXTS)
    a_target(tmp_path, name="pubmed-nemo", texts=TEXTS, **SISTER)
    a_target(tmp_path, name="pubmed-too", texts=TEXTS[:3] + ("five",), cleaning_version=2)
    roots = {n: ann.records_roots(seeder.current_snapshot(col.shard_dir_of(tmp_path, n)))
             for n in ("pubmed", "pubmed-nemo", "pubmed-too")}
    assert roots["pubmed"] == roots["pubmed-nemo"] != roots["pubmed-too"]
