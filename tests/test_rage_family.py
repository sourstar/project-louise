"""RAGE's family rule (operator, 2026-11-28: "An annotation refers to the
corpus of a collection, thus applies to all sisters and daughters, correct?"
— A; "Add this rule to RAGE"): app/rage/family.py, and annotations reading it."""
import json
import types

from app.ask import annotations as ask_ann
from app.rage import family


def C(name, cid, *, continues=None, derived_from=None, replaces=None):
    rep = types.SimpleNamespace(codex=replaces) if replaces else None
    return types.SimpleNamespace(name=name, codex=types.SimpleNamespace(
        collection_id=cid, continues=continues, derived_from=derived_from, replaces=rep))


def test_sisters_and_daughters_at_any_depth_are_one_family(tmp_path):
    held = [C("a", "A"), C("b", "B", derived_from="A"), C("c", "C", derived_from="B"),
            C("d", "D", continues="C"), C("e", "E", replaces="A"), C("x", "X")]
    for cid in "ABCDE":
        got = family.family(tmp_path, cid, held)
        assert got[0] == cid, "itself first"
        assert set(got) == set("ABCDE"), (cid, got)
    assert family.family(tmp_path, "X", held) == ("X",), "an unrelated collection is its own family"
    assert family.family(tmp_path, None, held) == ()


def test_an_id_no_longer_held_still_links_its_family(tmp_path):
    """The Codex a refit replaced, a mother retired: sisters meet through it."""
    held = [C("b", "B", derived_from="OLD"), C("c", "C", derived_from="OLD")]
    assert set(family.family(tmp_path, "B", held)) == {"B", "C", "OLD"}


def test_a_records_carry_is_a_link(tmp_path):
    from app.hold import carry
    (tmp_path / "nemo" / "state").mkdir(parents=True)
    (tmp_path / "nemo" / "state" / carry.MARKER).write_text(
        json.dumps({"done": True, "from": "G"}), encoding="utf-8")
    held = [C("nemo", "N"), C("g", "G")]
    assert set(family.family(tmp_path, "N", held)) == {"N", "G"}


def test_an_annotation_about_one_relative_applies_to_every_one(tmp_path):
    """Annotations target the mother; the daughter of her sister answers for it."""
    held = [C("mother", "M"), C("sister", "S", derived_from="M"), C("niece", "N", continues="S")]
    assert "M" in ask_ann.answers_for(tmp_path, "niece", "N", held)
    assert "N" in ask_ann.answers_for(tmp_path, "mother", "M", held), "and the other way round"
    assert ask_ann.answers_for(tmp_path, "alone", "Z", held) == ("Z",)
