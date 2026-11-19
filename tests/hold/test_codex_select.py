"""tests/hold/test_codex_select.py — the selection amendment (09 §9.3, Codex
version 12, operator 2026-11-18 option A; docs/notes/wiki-gutenberg-claude-plan.md
§12): a target of `annotates` may state the rule choosing which of its records
the claims are about, `select {id, version, sha256}`. Written for an annotations
Codex with a selected target ALONE, so no Codex already written moves; inside
`collection_id`, so another rule names another collection; refused below 12 and
on anything but an annotations Codex."""
import dataclasses
import hashlib
import json

import pytest

from app.hold import annotations as ann, codex as cx
from app.hold.codex import CodexError
from tests.hold.conftest import KEY, NOW, PLATFORM
from tests.hold.test_annotations import (TARGET_SNAPSHOT, a_claim, a_codex, a_target, claims_file,
                                         two_targets)

RULE = b'id = "wiki-gutenberg-select"\nversion = 1\n[rule]\nin = "memoir or history"\n'
SEL = cx.Select("wiki-gutenberg-select", 1, hashlib.sha256(RULE).hexdigest())


def selected(target, sel=SEL, **kw):
    return ann.build_codex(None, None, targets=[(target, TARGET_SNAPSHOT)], selects=[sel],
                           title="Selected", summary="Claims.", annotator="a test annotator",
                           platform_image=PLATFORM, nonce="0f" * 16, **kw)


def test_a_selected_target_makes_version_12_and_round_trips(tmp_path):
    target, _ = a_target(tmp_path)
    codex = selected(target)
    d = json.loads(codex.canonical())
    assert d["version"] == 12 and d["space"] is None
    assert d["annotates"] == [{"collection_id": target.collection_id, "from_snapshot": TARGET_SNAPSHOT,
                               "select": {"id": "wiki-gutenberg-select", "version": 1, "sha256": SEL.sha256}}]
    assert cx.loads(codex.canonical()) == codex
    assert cx.targets_of(codex)[0].select == SEL


def test_no_selection_keeps_every_version_and_its_bytes(tmp_path):
    target, _ = a_target(tmp_path)
    one = a_codex(target)
    assert one.version == 7 and "select" not in json.loads(one.canonical())["annotates"]
    assert a_codex(target, selects=[None]).canonical() == one.canonical()


def test_another_rule_names_another_collection(tmp_path):
    target, _ = a_target(tmp_path)
    other = cx.Select(SEL.id, 2, hashlib.sha256(RULE + b"#").hexdigest())
    assert selected(target).collection_id != selected(target, other).collection_id


def test_a_target_taken_whole_beside_a_selected_one_carries_no_key(tmp_path):
    text, entities, _ = two_targets(tmp_path)
    codex = ann.build_codex(None, None, targets=[(text, TARGET_SNAPSHOT), (entities, "2026-07")],
                            selects=[SEL, None], title="G", summary="C.", annotator="a",
                            platform_image=PLATFORM, nonce="0f" * 16)
    d = json.loads(codex.canonical())
    assert d["version"] == 12 and "select" in d["annotates"][0] and "select" not in d["annotates"][1]
    assert cx.loads(codex.canonical()) == codex


def test_what_version_12_refuses(tmp_path):
    target, _ = a_target(tmp_path)
    good = json.loads(selected(target).canonical())
    t0 = good["annotates"][0]

    def refused(match, **over):
        with pytest.raises(CodexError, match=match):
            cx.validate(cx.from_dict(dict(good, **over)))

    refused("select.sha256", annotates=[dict(t0, select=dict(t0["select"], sha256="AB" * 32))])
    refused("select.version", annotates=[dict(t0, select=dict(t0["select"], version=0))])
    refused("select.id", annotates=[dict(t0, select=dict(t0["select"], id=""))])
    refused("unknown field", annotates=[dict(t0, select=dict(t0["select"], extra=1))])
    refused("at least one target", annotates=[{k: v for k, v in t0.items() if k != "select"}])
    # below 12 the key is unknown: a version-9 Codex carrying it is refused
    refused("unknown field.*select", version=9)


def test_a_selection_is_refused_below_12_and_off_an_annotations_codex(tmp_path):
    target, _ = a_target(tmp_path)
    codex = selected(target)
    with pytest.raises(CodexError, match="declared exactly in a version 12"):
        cx.validate(dataclasses.replace(codex, version=9))
    with pytest.raises(CodexError, match="version 12 Codex is an annotations Codex"):
        cx.validate(dataclasses.replace(target, version=12))


def test_select_of_pins_the_files_bytes_and_refuses_a_rule_without_a_name(tmp_path):
    p = tmp_path / "rule.toml"
    p.write_bytes(RULE)
    assert ann.select_of(p) == SEL
    p.write_bytes(b'version = 1\n')
    with pytest.raises(ann.AnnotationsError, match="names itself"):
        ann.select_of(p)


def test_create_writes_a_version_12_collection_and_resumes_it(tmp_path):
    a_target(tmp_path)
    data = claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1", confidence="0.73"))
    kw = dict(target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT, claims=data, platform_image=PLATFORM,
              snapshot_id="2026-09", key=KEY, now=NOW, nonce="0f" * 16, log=lambda m: None, selects=[SEL])
    codex, snap = ann.create(tmp_path, "selected", **kw)
    assert codex.version == 12 and codex.annotates[0].select == SEL and snap.shards
    again, _ = ann.create(tmp_path, "selected", **kw)
    assert again.collection_id == codex.collection_id
    with pytest.raises(ann.AnnotationsError, match="one --select per"):
        ann.create(tmp_path, "other", **{**kw, "selects": [SEL, None]})
