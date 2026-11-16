"""The credits a result and a card carry (app/credits.py; 05 §5.3 `credits`,
added 2026-11-16, operator: "I accept the European Commission reuse
notice, add the credits if not already"): the Codex's corpus and archive
credits, and for EUR-Lex the vocabularies its subjects are labelled by."""
from types import SimpleNamespace

from app import credits
from app.ask import results
from app.plugins.eurlex import SOURCE as EURLEX
from app.refs import eurovoc


def _coll(plugin, *entries):
    cs = tuple(SimpleNamespace(kind=k, attribution=a) for k, a in entries)
    return SimpleNamespace(name=plugin, plugin=plugin, codex=SimpleNamespace(credits=cs))


def test_eurlex_credits_its_corpus_and_no_reference_it_does_not_show():
    """A credit follows what is shown (CLAUDE.md, 2026-11-16: a collection
    holds only the information contained in its corpus): EuroVoc's goes
    beside a label shown at ask time, never on every result."""
    got = credits.of(_coll("eurlex", ("corpus", EURLEX.corpus_attribution)))
    assert got == [EURLEX.corpus_attribution] and "© European Union" in got[0]
    assert "Decision 2011/833/EU" in eurovoc.ATTRIBUTION


def test_another_collection_credits_its_corpus_and_archives_each_once():
    got = credits.of(_coll("pubmed", ("corpus", "NLM"), ("archive", "MedCPT by NCBI"), ("archive", "NLM"),
                           ("community", "contributors")))
    assert got == ["NLM", "MedCPT by NCBI"], "the community entry names no source of the text"


def test_no_codex_no_credit():
    assert credits.of(SimpleNamespace(name="x", plugin="gutenberg", codex=None)) == []


def test_every_result_carries_its_collection_s_credits():
    ctx = {"collection_id": "c", "lineage_id": "l", "snapshot_id": "s", "trust_level": "checked",
           "certification": None, "red": False, "figures": None, "credits": ["© European Union"]}
    c = SimpleNamespace(name="eurlex", plugin="eurlex", codex=None)
    out = results.result_of(c, ctx, {"uid": "eurlex:1", "title": "t"}, 1, own_key=None, reranked=False)
    assert out["credits"] == ["© European Union"]
