"""NeuRIS's removed provisions (legal-chunking-retrieval.md §8), from the
forms München counted over NeuRIS's 68,288 article sections (2026-11-14):
the mark in the heading, or the mark or a dash as the whole body — and the
placeholders that are NOT a removal, never read as one."""
import pytest

from app.refs import neuris

H = "Bundesgesetz über X › Abschnitt 1"


@pytest.mark.parametrize("text", [
    f"{H} › § 2\n(weggefallen)",                                        # 397
    f"{H} › § 5\n(Weggefallen)",
    "§ 2\n(weggefallen)",                                               # no layer header
    f"{H} › §§ 1 bis 4 — (weggefallen)\n§§ 1 bis 4 — (weggefallen)",    # 1,059: the heading alone
    "Art 1 bis 28 — (weggefallen)",
    f"{H} › § 8 — Nährstoffvergleich(aufgehoben)\n§ 8 — Nährstoffvergleich(aufgehoben)",
    f"{H} › § 3 — (weggefallen)\n—",
    "Art 1\n-",                                                         # 1,014
    f"{H} › § 4\n- - -",
    f"{H} › § 44\n(aufgehoben)",
    f"{H} › § 2 — Tod im unmittelbaren Anschluß\n(Entfällt)",
    f"{H} › § 141\n(gegenstandslos)",
    f"{H} › § 142\n(gegenstandlos)",                                    # the origin's spelling
    f"{H} › § 4\n(gestrichen)",
    f"{H} › § 9\n(Durch Zeitablauf überholt)",
    f"{H} › § 10\n(ohne Inhalt)",
])
def test_a_removed_provision_is_read_as_removed(text):
    assert neuris.article_repealed(text)


@pytest.mark.parametrize("text", [
    f"{H} › § 1\n(Inkrafttreten)",                  # 56: an enacting clause left out
    f"{H} › § 3\n(Änderungsvorschrift)",
    f"{H} › § 3\n(Änderungsvorschriften)",
    f"{H} › § 9\n(Überleitungsvorschrift)",
    f"{H} › § 9\n(Übergangsvorschrift)",
    f"{H} › § 7\n...",
    f"{H} › §§ 1 bis 5\n§§ 1 bis 5",               # 226: a range heading, no body
    f"{H} › § 2\nDie Ausbildung dauert 3 Jahre.",
    f"{H} › § 2\n§ 17 gilt entsprechend.",
    f"{H} › § 6 — Begriffe\n(1) Ein Gesetz ist - wie hier - gültig.",
    f"{H} › § 6\n(1) Die Vorschrift ist aufgehoben worden, soweit sie X betrifft.",
    "",
])
def test_what_is_not_a_removal_is_never_read_as_one(text):
    assert not neuris.article_repealed(text)


def test_the_source_answers_the_pins_hook():
    assert neuris.NeurisRefSource().article_repealed(f"{H} › § 2\n(weggefallen)")
