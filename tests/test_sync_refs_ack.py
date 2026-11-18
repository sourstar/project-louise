"""`louise dev refs-sync --ack`: tlio's terms acknowledged byte-exact
through the reference-source resolution (app/licensing.py), eurovoc as
before, any other name refused (invariant 9)."""
from __future__ import annotations

import json

import pytest

from app import licensing
from tools import sync_refs


def test_ack_tlio_records_the_terms_hash_and_lifts_the_hold(tmp_path):
    base = tmp_path / "tlio"
    base.mkdir()
    (base / "TERMS.txt").write_bytes(b"no licence stated; non-commercial, local\r\n")
    assert licensing.ref_held_back(base, "tlio") is not None
    lines = sync_refs.ack(tmp_path, "tlio")
    rec = json.loads((base / "ack.json").read_text())
    assert rec["terms_sha256"] == "sha256:" + licensing.terms_sha256(base / "TERMS.txt")
    assert lines[0].endswith(licensing.terms_sha256(base / "TERMS.txt"))
    assert licensing.ref_held_back(base, "tlio") is None


def test_an_unknown_source_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no yellow or red reference source"):
        sync_refs.ack(tmp_path, "hgnc")
