"""A whole Codex created as it is (`hold collection create --recipe codex.json`,
no plugin): pubmed's parser-9 re-creation, 2026-11-08 — the live Codex with
its parser and its image changed, everything else byte for byte (RULE 1;
docs/notes/pubmed-other-abstracts.md). It takes the NAME given and the query
endpoint, and the members it names are copied from the held collection that
holds the same bytes, checked against the new Codex; and `dev carry` keeps
this peer's licence acknowledgement with the carried collection."""
from __future__ import annotations

import json

from app import louise
from app.hold import collection as col, recreate, shard as shard_mod
from tests.hold.test_collection_recreate import URL, held  # noqa: F401


def _whole(root, old, tmp_path) -> str:
    spec = json.loads(old.canonical())
    spec["toolchain"]["platform_image"] = "sha256:" + "9e" * 32       # the new image: a new id
    spec.pop("collection_id", None)
    p = tmp_path / "codex9.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return str(p)


def test_a_whole_codex_takes_its_name_and_the_members_it_names(held, tmp_path, capsys):  # noqa: F811
    root, old, _ = held
    assert louise.main(["hold", "collection", "create", "--root", str(root), "--recipe", _whole(root, old, tmp_path),
                        "--name", "old9", "--encoder", URL, "--query-encoder", URL + "/q",
                        "--api", "vega", "--embed-model", "vega-m", "--query-model", "vega-q"]) == 0
    out, err = capsys.readouterr()
    new_id = out.strip().splitlines()[-1]
    assert col.name_of(root, new_id) == "old9", "the name given, not the plugin's default"
    assert new_id != old.collection_id
    member, _sig = shard_mod.calibration_paths(old.space.calibration.set)
    assert (root / "old9" / "shard" / member).read_bytes() == (root / "old" / "shard" / member).read_bytes()
    assert "member: calibration — from old" in err
    held_file = next(ln.split("held: ", 1)[1] for ln in err.splitlines() if ln.startswith("held: "))
    from pathlib import Path
    assert URL + "/q" in Path(held_file).read_text(), "the query endpoint is in the collection file"


def test_dev_carry_keeps_the_licence_acknowledgement(held, tmp_path, monkeypatch, capsys):  # noqa: F811
    root, old, _ = held
    assert louise.main(["hold", "collection", "create", "--root", str(root), "--recipe", _whole(root, old, tmp_path),
                        "--name", "old9", "--encoder", URL, "--api", "vega", "--embed-model", "vega-m"]) == 0
    from app.hold import carry as carry_mod
    monkeypatch.setattr(carry_mod, "carry", lambda *a, **k: {"done": True})
    calls = []
    monkeypatch.setattr(recreate, "carry_ack", lambda r, f, t, c: calls.append((f, t, c.collection_id)) or "carried")
    assert louise.main(["dev", "carry", "old", "old9", "--root", str(root), "--rebuilt"]) == 0
    assert calls == [], "a dry run writes nothing"
    assert louise.main(["dev", "carry", "old", "old9", "--root", str(root), "--rebuilt", "--yes"]) == 0
    assert [c[:2] for c in calls] == [("old", "old9")]
    assert "licence acknowledgement: carried" in capsys.readouterr().err


def test_a_held_collection_without_a_space_is_no_source_and_a_rerun_completes(held, tmp_path, monkeypatch,  # noqa: F811
                                                                              capsys):
    """München, 2026-11-08: an annotations collection held beside them (its
    Codex has no space) crashed the search for the calibration member, and
    left the new collection's directory with codex.json alone. A space-less
    Codex is skipped; and the same command run again completes the members
    of the collection already written — no forget needed."""
    from types import SimpleNamespace
    root, old, _ = held
    real = col._held
    # FIRST, as on the node: the search for the source stops at a match
    monkeypatch.setattr(col, "_held", lambda r: [("notes", SimpleNamespace(
        collection_id="sha256:" + "ab" * 32, space=None, license=old.license))] + real(r))
    member, _sig = shard_mod.calibration_paths(old.space.calibration.set)
    args = ["hold", "collection", "create", "--root", str(root), "--recipe", _whole(root, old, tmp_path),
            "--name", "old9", "--encoder", URL, "--api", "vega", "--embed-model", "vega-m"]
    assert louise.main(args) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    (root / "old9" / "shard" / member).unlink()          # as a run stopped before its members
    assert louise.main(args) == 0, "the same command again: the collection written is taken as it is"
    again = capsys.readouterr()
    assert again.out.strip().splitlines()[-1] == first
    assert (root / "old9" / "shard" / member).read_bytes() == (root / "old" / "shard" / member).read_bytes()
    assert "member: calibration — from old" in again.err
