"""THE BOUNDED ENCODE of the legal chunker (2026-11-20; app/chunking_legal.py
`_spans`): a text read in spans cut at a space between two non-spaces gives
the SAME counts, offsets and pieces as one encode of the whole — held here
against the pinned Nemotron-3-Embed-1B tokenizer (sha256:797410df…, when
TOKENIZERS_DIR or LOUISE_TEST_TOKENIZER has it; the node's image does) and
a whitespace tokenizer, with spans forced to a few dozen characters so a
text is cut thousands of times. The second OOM hold measured why:
32018R0688 el's chunks rose 11.18 GB for 54.3 M characters in one encode."""
from __future__ import annotations

import os
import random
from pathlib import Path

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app import chunking_legal as cl
from app import config

PINNED = "797410dfb649a5b9ba92bc4fef7dbf4022d00e73de6867c4ac199a8846439421"


def _pinned() -> Tokenizer | None:
    for p in (os.environ.get("LOUISE_TEST_TOKENIZER"), str(Path(config.TOKENIZERS_DIR) / f"{PINNED}.json")):
        if p and Path(p).is_file():
            return Tokenizer.from_file(p)
    return None


def _words() -> Tokenizer:
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    return tok


PIECES = ["Άρθρο", "1", "Ο", "παρών", "κανονισμός", "εφαρμόζεται", "στις", "αεροπορικές", "εταιρείες,",
          "«ΕΕ»", "L 119/1", "2018/688", "Artikel", "Abs.", "Straßenverkehrs­ordnung", "l'Union", "européenne",
          "d’application", "—", "(a)", "aa)", "1°", "i)", "[INST]", "<s>", "</s>", "12,5", "13.457,00",
          "€", "%", "§", "→", "😀", "日本語", "عربي", "naïve", "é", "x/y", "a\r\nb", "\t", " ",
          " ", "....", "1.", "2-bis.", "PART", "ÜBER", "ΤΙΤΛΟΣ", "COM(2018)", "http://eur-lex.europa.eu"]
SEPS = [" ", " ", " ", " ", "  ", "\n", "\n\n", " \n", "\t", ""]


def _text(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(PIECES) + rng.choice(SEPS) for _ in range(n))


def _pinned_or_fail() -> Tokenizer:
    tok = _pinned()
    if tok is None:
        pytest.fail("LOUISE_REQUIRE_PINNED_TOKENIZER: the pinned tokenizer is not in TOKENIZERS_DIR")
    return tok


#: the deploy runs this file with LOUISE_REQUIRE_PINNED_TOKENIZER=1 in the
#: staged image, so the identity is proved there against the very file the
#: worker loads — never skipped where it matters
TOKENIZERS = [pytest.param(_words, id="whitespace")] + [
    pytest.param(_pinned_or_fail, id="pinned",
                 marks=pytest.mark.skipif(_pinned() is None and not os.environ.get("LOUISE_REQUIRE_PINNED_TOKENIZER"),
                                          reason="the pinned tokenizer is not here"))]


@pytest.fixture
def small_spans(monkeypatch):
    monkeypatch.setattr(cl, "SPAN_CHARS", 37)


@pytest.mark.parametrize("make", TOKENIZERS)
def test_the_spans_count_and_offsets_are_the_whole_texts(make, small_spans):
    tok = make()
    assert cl.spans_are_exact(tok)
    rng = random.Random(20261120)
    for _ in range(300):
        text = _text(rng, rng.randint(1, 400))
        whole = tok.encode(text, add_special_tokens=False)
        assert cl._count(tok, text) == len(whole.ids)
        assert list(cl._token_ends(tok, text)) == [e for _s, e in whole.offsets]
        assert len(cl._spans(tok, text)) > 1 or len(text) <= cl.SPAN_CHARS + 200 or " " not in text


@pytest.mark.parametrize("make", TOKENIZERS)
@pytest.mark.parametrize("budget", [1, 2, 7, 64])
def test_the_windows_are_the_listed_ones(make, budget, small_spans):
    tok = make()
    rng = random.Random(budget)
    for _ in range(150):
        text = _text(rng, rng.randint(0, 300))
        assert cl._windows(tok, text, budget) == cl._windows_listed(tok, text, budget)


@pytest.mark.parametrize("make", TOKENIZERS)
def test_a_split_is_byte_for_byte_the_unbounded_one(make, monkeypatch):
    """The chunker's own cut, every level: spans of 37 characters against
    spans of none (SPAN_CHARS past the text: one encode, as before)."""
    tok = make()
    rng = random.Random(688)
    for _ in range(60):
        text = _text(rng, rng.randint(50, 1500))
        for budget in (8, 40, 300):
            monkeypatch.setattr(cl, "SPAN_CHARS", 10 ** 12)
            want = cl.split(tok, text, budget)
            monkeypatch.setattr(cl, "SPAN_CHARS", 37)
            assert cl.split(tok, text, budget) == want


def test_no_encode_reads_more_than_a_span(small_spans):
    """The bound itself: what one encode is given."""
    tok = _words()
    seen: list[int] = []

    class Counting:
        def encode(self, text, **k):
            seen.append(len(text))
            return tok.encode(text, **k)

        def to_str(self):
            return tok.to_str()
    c = Counting()
    text = " ".join(f"w{i}" for i in range(5000))
    cl._count(c, text)
    list(cl._token_ends(c, text))
    assert max(seen) <= cl.SPAN_CHARS + 10 and len(seen) > 100


def test_a_tokenizer_not_declared_exact_is_encoded_whole(small_spans):
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Metaspace()
    assert not cl.spans_are_exact(tok)
    text = " ".join(["a"] * 100)
    assert cl._spans(tok, text) == [(0, len(text))]


def test_a_run_with_no_safe_space_is_one_span(small_spans):
    """The 500 x's are never cut inside; the cut is the first safe space past
    SPAN_CHARS, before the y. (Until 2026-11-23 this expected the whole text
    as one span, and passed only when the tokenizer inherited the verdict of
    the previous test's non-exact one at the same id: `spans_are_exact`.)"""
    tok = _words()
    assert cl.spans_are_exact(tok)
    text = "x" * 500 + " y"
    assert cl._spans(tok, text) == [(0, 500), (500, len(text))]
    assert cl._count(tok, text) == len(tok.encode(text, add_special_tokens=False).ids)


def test_a_verdict_is_never_inherited_by_another_tokenizer_at_the_same_id(small_spans):
    """`id()` is reused once an object is collected (München, 2026-11-23: 2
    failed in 6 staged runs). The verdict is the tokenizer's own: an entry
    for its id that holds another object is judged again."""
    exact, other = _words(), Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    other.pre_tokenizer = pre_tokenizers.Metaspace()
    assert cl.spans_are_exact(exact)
    cl._exact[id(other)] = (exact, True)                 # what a dead exact tokenizer at this id left
    assert not cl.spans_are_exact(other)
    assert cl._exact[id(other)][0] is other


def test_the_verdicts_hold_their_tokenizers_and_are_bounded(small_spans):
    toks = [_words() for _ in range(cl._EXACT_HELD + 5)]
    for t in toks:
        assert cl.spans_are_exact(t)
    assert len(cl._exact) <= cl._EXACT_HELD
    assert all(cl._exact[id(t)][0] is t for t in toks[-cl._EXACT_HELD:]), "held while their verdict stands"


def test_the_identity_tool_compares_the_two_reads(monkeypatch):
    """tools/measure/legal_chunk_identity.py: the worker's own `chunks`,
    read in spans and whole; a difference is named."""
    from app import parallel
    from tools.measure import legal_chunk_identity as lci
    seen: list[int] = []

    def fake(kind, collection, rows):
        seen.append(cl.SPAN_CHARS)
        return [(True, [{"id": "eurlex:1:0:3", "chapter": "Art. 1", "text": "x" * (5 if cl.SPAN_CHARS < 10 ** 9 else 6)}])]
    monkeypatch.setattr(parallel, "_compute", fake)
    got = lci.compare(object(), {"uid": "eurlex:1", "size": 10})
    assert seen == [cl.SPAN_CHARS, 10 ** 15] and got["identical"] is False and got["first_difference"] == 0
    monkeypatch.setattr(parallel, "_compute", lambda kind, c, rows: [(True, [{"id": "a", "chapter": "", "text": "t"}])])
    assert lci.compare(object(), {"uid": "eurlex:1", "size": 10})["identical"] is True


def test_the_identity_samples_only_documents_with_text(tmp_path):
    import sqlite3
    from types import SimpleNamespace

    from tools.measure import legal_chunk_identity as lci
    con = sqlite3.connect(tmp_path / "m.sqlite")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents(uid TEXT, size INT, error TEXT, in_scope INT, chunks INT)")
    con.executemany("INSERT INTO documents VALUES(?,?,?,?,?)", [
        ("n:1", 900, None, 1, 0), ("n:2", 800, None, 1, 4), ("n:3", 700, None, 0, 4),
        ("n:4", 600, "parked", 1, 4), ("n:5", 500, None, 1, None), ("n:6", 400, None, 1, 2)])
    largest = lci._rows(con, SimpleNamespace(uids=None, largest=5, every=1, limit=None))
    assert [r["uid"] for r in largest] == ["n:2", "n:6"], "no textless, out-of-scope or parked row"
    every = lci._rows(con, SimpleNamespace(uids=None, largest=None, every=1, limit=None))
    assert [r["uid"] for r in every] == ["n:2", "n:6"]
