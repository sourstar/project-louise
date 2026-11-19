"""tools/measure/pg_wikidata_match.py: the strict keys (author years, title forms), offline."""
from tools.measure import pg_wikidata_match as m


def test_an_author_needs_both_years_and_keeps_its_surname():
    assert m.pg_author("Garibaldi, Giuseppe, 1807-1882") == ("garibaldi", 1807, 1882)
    assert m.pg_author("Rosen, Erwin, 1876-1923 [Translator]") == ("rosen", 1876, 1923)
    assert m.pg_author("Anonymous") is None
    assert m.pg_author("Homer, 751? BCE-651? BCE") is None


def test_an_author_is_matched_only_when_one_person_fits():
    by = {(1807, 1882): {("Q1", "giuseppe garibaldi"), ("Q2", "anita garibaldi")}}
    assert m.author_qid(("garibaldi", 1807, 1882), by) is None, "two people with the years: no match"
    by = {(1807, 1882): {("Q1", "giuseppe garibaldi")}}
    assert m.author_qid(("garibaldi", 1807, 1882), by) == "Q1"
    assert m.author_qid(("garibaldi", 1807, 1883), by) is None


def test_the_title_is_cut_at_its_subtitle_and_capitalised():
    assert m.title_forms("Kit and Kitty: A Story of West Middlesex") == ["Kit and Kitty"]
    assert m.title_forms("dope") == ["dope", "Dope"]
    assert m.title_forms("The Rise of the Dutch Republic — Volume 01") == ["The Rise of the Dutch Republic"]


def test_a_literal_is_escaped():
    assert m._lit('A "quoted" \\ title') == '"A \\"quoted\\" \\\\ title"'
