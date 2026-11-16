"""tools/measure/eurlex_history.py: the history slice's size per language,
from parser 4's own listing (the in-force asked with each page), counted by
where each unit is or would be — no unit fetched."""
from tests.acquire.test_cellar import ACT, CONS, _spec, listing_cache, mock, no_wait  # noqa: F401

from app.plugins.eurlex import source as src
from tools.measure import eurlex_history

REPEALED = "32016R1037"


def test_the_listing_counts_base_and_history_held_and_not(mock, tmp_path):
    mock.add(REPEALED, "fr", ACT, in_force="false")
    corpus = tmp_path / "corpus"
    held = corpus / src.unit_rel("fr", ACT)               # the dump gave it
    held.mkdir(parents=True)
    (held / "tree.json").write_text("{}")
    spec = _spec(mock)
    got = eurlex_history.count_language(corpus, "fr", spec=spec, endpoint=spec.endpoints[0],
                                        years=range(2016, 2017))
    assert got == {"language": "fr", "base_held": 1, "base_not_held": 0, "history_held": 0,
                   "history_not_held": 2, "history_consolidated": 1}
    assert not [r for r in mock.requests if r[0] == "GET"], "nothing but the listing asked"
