"""The judged probe of a literary source counted as evidence
(tools/measure/work_framing.py): its check, on answers shaped as the node's."""
from tools.measure import work_framing as wf

SOURCES = [{"n": 1, "work": None}, {"n": 2, "work": None},
           {"n": 9, "work": {"kind": "a literary work", "basis": "by its Gutenberg category"}}]


def test_a_novel_counted_among_a_facts_confirmations_is_flagged():
    got = wf.as_evidence("Napoleon died on May 5, 1821 [1][2][9]. All these sources agree.", SOURCES)
    assert got == ["Napoleon died on May 5, 1821 [1][2][9]."]


def test_a_novel_cited_for_what_it_narrates_or_alone_is_not():
    assert wf.as_evidence("In the novel, Napoleon's young neighbour mourns him [9].", SOURCES) == []
    assert wf.as_evidence("Napoleon died on May 5, 1821 [1][2]. The novel retells his last days [9].", SOURCES) == []
    assert wf.as_evidence("The romance imagines his exile [2][9].", SOURCES) == []


def test_every_question_has_a_kind_and_the_controls_are_münchens():
    kinds = {k for _i, _q, k in wf.QUESTIONS}
    assert kinds == {"fact_with_fiction", "control"}
    assert {i for i, _q, k in wf.QUESTIONS if k == "control"} == {"cinque-maggio", "anabasis", "pericles"}


def test_a_literary_work_the_question_names_is_not_evidence_misused():
    """Architecture, 2026-11-27: the control asks ABOUT the poem, so citing it
    is right; München's flagged sentence is scored clean. The defect's own
    sentence on la-rochelle still is not."""
    sources = [{"n": 1, "title": "Il cinque maggio", "work": {"kind": "a literary work", "basis": "x"}},
               {"n": 2, "title": "Storia di Napoleone", "work": None}]
    q = "Che cosa dice Il cinque maggio di Manzoni su Napoleone?"
    assert wf.as_evidence("In sintesi, *Il cinque maggio* ritrae Napoleone come un uomo caduto [1][2].", sources, q) == []
    assert wf.as_evidence("Napoleone morì nel 1821 [1][2].", sources, "Quando morì Napoleone?") == \
        ["Napoleone morì nel 1821 [1][2]."]
    rochelle = [{"n": 5, "title": "The Three Musketeers", "work": {"kind": "a literary work", "basis": "x"}},
                {"n": 8, "title": "Richelieu", "work": None}]
    assert wf.as_evidence("He is described as showing great courage during the siege [5][8].", rochelle,
                          "What did Cardinal Richelieu do at the siege of La Rochelle?") != []
