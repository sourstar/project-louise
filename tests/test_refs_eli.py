"""The `eli` namespace's one owner (app/refs/eli.py; legal-fr-de-eu.md §5.2):
a router by jurisdiction, holding nothing itself."""
from app import refs
from app.refs import eli


def test_a_key_is_routed_to_the_source_declaring_its_jurisdiction():
    src = refs.REFS_REGISTRY["eli-refs"]
    assert src.namespaces == ("eli",) and src.schema().tables == ()
    assert src.route("eli:it:id/2003/11/15/003G0333", refs.REFS_REGISTRY) is refs.REFS_REGISTRY["normattiva-refs"]
    assert src.route("eli:IT:id/x", refs.REFS_REGISTRY) is refs.REFS_REGISTRY["normattiva-refs"]
    assert src.route("eli:zz:id/x", refs.REFS_REGISTRY) is None
    assert src.route("eli:it", refs.REFS_REGISTRY) is None


def test_one_owner_per_namespace_and_one_source_per_jurisdiction():
    """06 §6.5: a namespace has one owner; a jurisdiction one holder."""
    owners: dict[str, str] = {}
    held: dict[str, str] = {}
    for sid, source in refs.REFS_REGISTRY.items():
        for ns in tuple(getattr(source, "namespaces", ()) or ()):
            assert ns not in owners, f"{ns} registered by {owners.get(ns)} and {sid}"
            owners[ns] = sid
        jur = getattr(source, "eli_jurisdiction", None)
        if jur:
            assert jur not in held, f"eli:{jur} held by {held.get(jur)} and {sid}"
            held[jur] = sid
    assert owners["eli"] == "eli-refs" and held["it"] == "normattiva-refs"


def test_the_parts_of_a_canonical_eli():
    assert eli.jurisdiction_of("eli:fr:jo/2026/10/28/0252") == "fr"
    assert eli.path_of("eli:fr:jo/2026/10/28/0252") == "jo/2026/10/28/0252"
    assert eli.jurisdiction_of("urn:nir:x") is None and eli.jurisdiction_of("eli:") is None
