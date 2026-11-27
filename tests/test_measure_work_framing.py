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
