"""The card for a person's question (docs/notes/ask-card-workflow.md;
app/question_card.py): the worker unloads the corpus encoders, idle first,
a busy one asked again, a gate's or a pass's never; the web unloads the
query encoders after the search and loads the answer's models behind it;
the keeper loads nothing while the question holds the claim."""
from __future__ import annotations

import threading

import pytest

from app import model_load, model_pins, question_card, resident
from app.resident import Resident

S = "http://localai:8080"


def _r(model, *roles):
    return Resident(S, model, tuple(roles))


class Card:
    """LocalAI as the card's models see it: what is loaded, what is busy
    (refuses a shutdown `refuse[model]` more times), what is pinned."""

    def __init__(self, loaded, busy=(), pinned=(), refuse=None):
        self.loaded = set(loaded)
        self.busy = set(busy)
        self.pinned = set(pinned)
        self.refuse = dict(refuse or {})
        self.asked: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(model_load, "loaded_models", lambda server, max_age_s=None: frozenset(self.loaded))
        monkeypatch.setattr(model_load, "activity",
                            lambda server: {m: (80.0 if m in self.busy else 0.0) for m in self.loaded})
        monkeypatch.setattr(model_pins, "read_pinned", lambda server, model: model in self.pinned)
        monkeypatch.setattr(model_load, "unload", self.unload)
        return self

    def unload(self, url, model):
        self.asked.append(model)
        if model not in self.loaded:
            return False
        if self.refuse.get(model, 0) > 0:
            self.refuse[model] -= 1
            return False
        self.loaded.discard(model)
        return True


def test_only_a_model_that_only_encodes_documents_is_a_corpus_encoder():
    want = [_r("chat", "chat"), _r("madlad", "translation"), _r("bge-m3", "query encoder", "encoder"),
            _r("nemotron", "encoder"), _r("medcpt-article", "encoder"), _r("rerank", "reranker")]
    assert [r.model for r in question_card.corpus_encoders(want)] == ["nemotron", "medcpt-article"]


def test_the_corpus_encoders_go_idle_first_a_busy_one_asked_again_a_pinned_one_never(monkeypatch):
    card = Card(loaded={"nemotron", "dar", "medcpt-article", "gate-enc", "chat"}, busy={"nemotron"},
                pinned={"gate-enc"}, refuse={"nemotron": 2}).install(monkeypatch)
    slept = []
    want = [_r("nemotron", "encoder"), _r("dar", "encoder"), _r("medcpt-article", "encoder"),
            _r("gate-enc", "encoder"), _r("chat", "chat"), _r("off-card", "encoder")]
    done = question_card.free_card(want, question=lambda: True, sleep=slept.append)
    assert done == ["dar", "medcpt-article", "nemotron"], "idle first, the busy one when it lets go"
    assert card.asked[:3] == ["dar", "medcpt-article", "nemotron"]
    assert card.asked.count("nemotron") == 3 and len(slept) == 2
    assert "gate-enc" in card.loaded and "gate-enc" not in card.asked, "a gate's or a pass's pin is kept"
    assert "chat" in card.loaded and "off-card" not in card.asked


def test_a_busy_encoder_is_asked_a_bounded_number_of_times(monkeypatch):
    card = Card(loaded={"nemotron"}, busy={"nemotron"}, refuse={"nemotron": 99}).install(monkeypatch)
    said = []
    monkeypatch.setattr(question_card, "_log", [lambda level, text: said.append((level, text))])
    assert question_card.free_card([_r("nemotron", "encoder")], question=lambda: True,
                                   sleep=lambda s: None) == []
    assert card.asked.count("nemotron") == question_card.UNLOAD_TRIES
    assert said and said[-1][0] == "warn" and "left to the reclaimer" in said[-1][1]


def test_the_tries_stop_when_the_question_ends(monkeypatch):
    card = Card(loaded={"a", "b"}).install(monkeypatch)
    state = {"open": True}

    def question():
        was = state["open"]
        state["open"] = False                # ends after the first unload
        return was
    assert question_card.free_card([_r("a", "encoder"), _r("b", "encoder")], question=question,
                                   sleep=lambda s: None) == ["a"]
    assert "b" in card.loaded


def test_the_watcher_frees_the_card_once_per_question(monkeypatch):
    calls = []
    monkeypatch.setattr(question_card, "on_question", lambda want=None: calls.append(1))
    claims = iter([False, True, True, True, False, True, False])
    beats = {"n": 0}

    def stop():
        beats["n"] += 1
        return beats["n"] > 7
    question_card.watch(stop, question=lambda: next(claims), sleep=lambda s: None)
    assert len(calls) == 2, "one rising edge, one call"


def test_on_question_releases_the_pins_at_once_and_unloads_behind(monkeypatch):
    released = []
    monkeypatch.setattr(model_pins, "release_for_question", lambda: released.append(1) or [])
    gate = threading.Event()
    got = []

    def free(want, **_kw):
        gate.wait(5)
        got.append(list(want))
        return []
    monkeypatch.setattr(question_card, "free_card", free)
    monkeypatch.setattr(question_card, "_freeing", [None])
    question_card.on_question(lambda: [_r("nemotron", "encoder")])
    question_card.on_question(lambda: [_r("other", "encoder")])     # one pass at a time
    assert released == [1, 1], "the pins go at once, every time"
    gate.set()
    question_card._freeing[0].join(5)
    assert [[r.model for r in w] for w in got] == [["nemotron"]]


def test_after_the_search_the_query_encoders_go_and_the_answer_s_models_load(monkeypatch, isolated_config):
    from app import collections as collections_mod, config, llm, retrieval, translate
    card = Card(loaded={"vega-4b", "gate-q"}, pinned={"gate-q"}).install(monkeypatch)
    monkeypatch.setattr(question_card, "_query_encoders", lambda cols: [(S, "vega-4b"), (S, "gate-q")])
    monkeypatch.setattr(question_card, "_tts", lambda: (S, "omnivoice"))
    warmed = []
    monkeypatch.setattr(llm, "warm", lambda *a, **k: warmed.append("chat"))
    monkeypatch.setattr(translate, "warm", lambda: warmed.append("madlad"))
    monkeypatch.setattr(resident, "_speech_warm", lambda s, m, kind, role: lambda: warmed.append(m))
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: type("R", (), {"url": S, "model": "rerank"})())
    monkeypatch.setattr(retrieval, "_warm_reranker", lambda r: warmed.append("rerank"))
    monkeypatch.setattr(config, "CHAT_URL", S)
    monkeypatch.setattr(config, "CHAT_MODEL", "chat")
    monkeypatch.setattr(config, "TRANSLATE_URL", S)
    monkeypatch.setattr(config, "TRANSLATE_MODEL", "madlad")
    question_card.after_search(["gutenberg", "pubmed"], background=False)
    assert "vega-4b" not in card.loaded and "gate-q" in card.loaded, "a pinned query encoder stays"
    assert warmed == ["chat", "madlad", "omnivoice", "rerank"], "in the order the answer meets them"
    warmed.clear()
    question_card.after_search(["gutenberg"], speed=True, background=False)
    assert "madlad" not in warmed, "Speed: the chat model translates; MADLAD is not loaded for it"


def test_before_the_search_tts_goes_only_if_the_query_encoders_do_not_fit(monkeypatch):
    card = Card(loaded={"omnivoice"}).install(monkeypatch)
    monkeypatch.setattr(question_card, "_query_encoders", lambda cols: [(S, "vega-4b")])
    monkeypatch.setattr(question_card, "_tts", lambda: (S, "omnivoice"))
    monkeypatch.setattr(model_load, "size_vram", lambda s, m: 6_000 * 2**20)
    monkeypatch.setattr(resident, "_threshold", lambda s: 0.95)
    monkeypatch.setattr(resident, "_card", lambda: (32_656 * 2**20, 24_000 * 2**20))
    assert question_card.before_search(["gutenberg"]) is None and "omnivoice" in card.loaded, "it fits"
    monkeypatch.setattr(resident, "_card", lambda: (32_656 * 2**20, 27_000 * 2**20))
    assert question_card.before_search(["gutenberg"]) == "omnivoice" and "omnivoice" not in card.loaded


def test_the_keeper_loads_nothing_while_a_question_holds_the_claim(monkeypatch):
    started = []
    monkeypatch.setattr(resident, "_start_warm", lambda due, stop: started.append(due) or due)
    monkeypatch.setattr(resident, "_question_now", lambda: True)
    monkeypatch.setattr(resident, "preloads", lambda state=None: (True, ""))
    r = Resident(S, "chat", ("chat",), warm=lambda: None)
    monkeypatch.setattr(resident, "wanted", lambda served, embed_mode, sw=None: [r])
    monkeypatch.setattr(resident, "_loaded", lambda r: False)
    monkeypatch.setattr(resident, "_release_orphan_pins", lambda want, now: None)
    monkeypatch.setattr(resident, "_remember", lambda want: resident.Memory())
    monkeypatch.setattr(resident, "_say", lambda *a: None)
    out = resident.beat([], embed_mode=lambda c: "all")
    assert out["question"] is True and out["warming"] == [] and started == []
    monkeypatch.setattr(resident, "_question_now", lambda: False)
    out = resident.beat([], embed_mode=lambda c: "all")
    assert out["warming"] == [r.key], "the keeper resumes when the claim ends"
