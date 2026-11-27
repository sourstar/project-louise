"""tests/test_chunker_fixed.py — `fixed` is `book` with its window declared.

docs/06-plugins.md §6.3 registers `fixed` and says, immediately above its
table, that **a chunker reads no node configuration**. `chunk_book` reads
`CHUNK_TOKENS` and `CHUNK_OVERLAP` from the environment, so two peers could
agree on a `space_id` (09 §9.3 hashes the layer's params) and disagree on
chunks — invariant 1's failure mode, and undetectable, because both peers
computed the id the document prescribes and both are honest.

`fixed` closes that. It is the same windows with the two numbers carried in
the layer, and these tests pin the two things that makes true: that it cuts
identically, and that it refuses to guess.
"""
import pytest

from app import chunking, config
from app.chunking import ChunkerError

BOOK = [
    ("Chapter 1", "\n\n".join(f"Paragraph {i} of the first chapter, with "
                              f"enough words in it to be worth cutting and "
                              f"re-cutting several times over." for i in range(60))),
    ("Chapter 2", "One short paragraph."),
    ("Chapter 3", "x" * 30_000),          # forces the hard-split path
]


def ids(gen):
    return [(c["id"], c["seq"], c["chapter"], c["text"]) for c in gen]


# ---- the identity that lets legacy vectors carry over ---------------------

def test_fixed_at_the_config_defaults_cuts_exactly_like_book():
    """The load-bearing claim. `book` and `fixed` share `_fixed_windows`, so
    this is true by construction — and pinned here because everything else
    rests on it: if the two cut identically, a Codex collection declaring
    `fixed` has the chunk ids a legacy pack already holds, and its vectors
    carry over instead of being re-embedded."""
    book = ids(chunking.chunk_book("gutenberg:1342", 1342, BOOK, 1))
    fixed = ids(chunking.chunk_fixed(
        "gutenberg:1342", 1342, BOOK, 1,
        {"tokens": config.CHUNK_TOKENS, "overlap": config.CHUNK_OVERLAP}))
    assert book == fixed
    assert len(book) > 3, "the fixture must actually exercise the windowing"


def test_the_chunk_ids_are_the_same_string():
    """A chunk id is `{uid}:{seq}:{version}` (09 §9.5), so the VERSION has to
    match too — not just the boundaries. `FIXED_CHUNK_VERSION` is 1 because
    `config.CHUNK_VERSION` is, which is what makes the ids equal rather than
    merely the texts."""
    assert chunking.chunk_version_for(chunking.FIXED) == \
        chunking.chunk_version_for(chunking.BOOK) == config.CHUNK_VERSION

    v = chunking.chunk_version_for(chunking.FIXED)
    book = [c["id"] for c in chunking.chunk_book("g:1", 1, BOOK, v)]
    fixed = [c["id"] for c in chunking.chunk_fixed(
        "g:1", 1, BOOK, v,
        {"tokens": config.CHUNK_TOKENS, "overlap": config.CHUNK_OVERLAP})]
    assert book == fixed


def test_a_different_window_cuts_differently():
    """Otherwise the test above would pass for a chunker that ignored its
    parameters entirely, which is the bug being fixed."""
    a = ids(chunking.chunk_fixed("g:1", 1, BOOK, 1, {"tokens": 1000, "overlap": 120}))
    b = ids(chunking.chunk_fixed("g:1", 1, BOOK, 1, {"tokens": 300, "overlap": 30}))
    assert a != b
    assert len(b) > len(a)


# ---- it refuses to guess --------------------------------------------------

def test_fixed_refuses_a_layer_that_does_not_say():
    """No defaults, and no reading the environment. A chunker that guessed
    would reintroduce exactly the divergence it exists to remove."""
    for params in (None, {}, {"tokens": 1000}, {"overlap": 120}):
        with pytest.raises(ChunkerError, match="no defaults"):
            list(chunking.chunk_fixed("g:1", 1, BOOK, 1, params))


def test_fixed_does_not_read_the_environment(monkeypatch):
    """The property the whole change is for: move the node's configuration
    and `fixed` does not move with it."""
    p = {"tokens": 1000, "overlap": 120}
    before = ids(chunking.chunk_fixed("g:1", 1, BOOK, 1, p))
    monkeypatch.setattr(config, "CHUNK_TOKENS", 250)
    monkeypatch.setattr(config, "CHUNK_OVERLAP", 25)
    assert ids(chunking.chunk_fixed("g:1", 1, BOOK, 1, p)) == before

    # ...and `book` DOES, which is why it stays grandfathered and why a new
    # collection uses `fixed` instead.
    assert ids(chunking.chunk_book("g:1", 1, BOOK, 1)) != before


def test_an_overlap_at_or_above_the_window_is_refused():
    """`book` clamps this because it is reachable from configuration alone;
    `fixed` refuses it, because a layer is a declaration and a declaration
    that cannot terminate is a mistake to report, not to repair."""
    with pytest.raises(ChunkerError, match="yields for ever"):
        list(chunking.chunk_fixed("g:1", 1, BOOK, 1,
                                  {"tokens": 100, "overlap": 100}))
    with pytest.raises(ChunkerError, match="at least 1"):
        list(chunking.chunk_fixed("g:1", 1, BOOK, 1,
                                  {"tokens": 0, "overlap": 0}))


# ---- what must NOT have moved --------------------------------------------

def test_fixed_is_registered_and_book_is_untouched():
    assert chunking._CHUNKERS[chunking.FIXED] is chunking.chunk_fixed
    assert chunking._CHUNKERS[chunking.BOOK] is chunking.chunk_book
    # wikidata-text (2026-10-19): the Wikidata plugin's own chunker, a token
    # window of a pinned tokenizer at the textifier's statement boundaries
    # (06 §6.3); it enters only the spaces that declare it
    # legal_article (2026-11-08): the shared legal space's, app/chunking_legal.py
    assert set(chunking._CHUNKERS) == {"book", "fixed", "verbatim", "abstract", "cdc",
                                       "akn_article", "wikidata-text", "legal_article"}


def test_every_chunker_takes_the_same_shape():
    """The dispatch passes `(uid, nid, sections, version, params)` blind, so
    a chunker with another signature is a TypeError at cut time — on a
    corpus, in a worker, hours in."""
    import inspect
    for name, fn in chunking._CHUNKERS.items():
        args = list(inspect.signature(fn).parameters)
        assert args[:5] == ["uid", "native_id", args[2], "version", "params"], \
            f"{name} has signature {args}"


# ---- the two places an identity could have moved, and did not ------------

def test_params_do_not_enter_the_legacy_space_id():
    """`layers.space_payload` is what `app/spaces.py` folds into the LEGACY
    space id, and the vectors already on this node are keyed to it. It
    returns `{}` for a one-layer collection and `[[chunker, version]]`
    otherwise — params in NEITHER. Checked rather than assumed, because if
    it were otherwise every existing collection would refuse to start
    (invariant 1) the moment a layer gained a parameter."""
    from app import layers as layers_mod
    import inspect
    src = inspect.getsource(layers_mod.space_payload)
    assert "params" not in src, \
        "space_payload must not carry params, or every pinned space id moves"


def test_a_manifest_is_unchanged_when_a_layer_has_no_params():
    """Every collection on this node predates params. A manifest that gained
    an empty key would differ from the bytes already on disk for no reason."""
    from app.layers import Layer
    assert Layer("book", 1, "default").as_manifest() == \
        {"chunker": {"id": "book", "version": 1}, "rule": "default"}


def test_a_manifest_carries_params_when_a_layer_has_them():
    from app.layers import Layer
    m = Layer("fixed", 1, "default", {"tokens": 1000, "overlap": 120}).as_manifest()
    assert m["chunker"]["params"] == {"tokens": 1000, "overlap": 120}


def test_the_layer_parser_reads_params_and_refuses_a_non_table():
    from app import layers as layers_mod
    from app.layers import LayerError
    got, = layers_mod.parse_layers(
        [{"chunker": "fixed", "params": {"tokens": 800, "overlap": 80}}],
        where="test")
    assert got.chunker == "fixed" and got.params == {"tokens": 800, "overlap": 80}
    assert layers_mod.parse_layers([{"chunker": "book"}], where="t")[0].params == {}

    with pytest.raises(LayerError, match="params is a table"):
        layers_mod.parse_layers([{"chunker": "fixed", "params": 7}], where="t")


def test_a_bundle_refuses_the_legacy_bare_hex_space_id():
    """Item 3 needs no code change — the two `space_id` functions name
    different things and never meet — but this is the assertion that keeps
    it true. `app/spaces.py` produces a bare 32-hex BLAKE2b digest; 09 §9.3
    fixes `sha256:` and 64 lowercase hex, and a conformant bundle therefore
    CANNOT carry the legacy value."""
    from app.hold import bundle as bd
    from app.hold.bundle import BundleError
    from tests.hold.test_bundle import header

    with pytest.raises(BundleError, match="sha256:"):
        bd.check(header(space_id="41" * 16))     # the legacy form: bare, 32 hex
    bd.check(header())                            # the Codex's form passes


# ---- the hard split cuts by index: the same windows, without the copies -------

def _reference_windows(uid, native_id, chapters, version, max_chars, overlap_chars):
    """`_fixed_windows` as it was until 2026-10-20 — the remainder re-sliced
    once per window — kept here as the oracle the rewrite must equal byte
    for byte: every pinned space id rests on these windows (invariant 3)."""
    seq = 0
    for chapter_title, body in chapters:
        paras = [p for p in body.split("\n\n") if p.strip()]
        buf, size = [], 0
        for para in paras:
            p = para.strip()
            while len(p) > max_chars:
                head, p = p[:max_chars], p[max_chars - overlap_chars:]
                yield chunking._mk(uid, native_id, seq, chapter_title, head, version)
                seq += 1
            if size + len(p) > max_chars and buf:
                text = "\n\n".join(buf)
                yield chunking._mk(uid, native_id, seq, chapter_title, text, version)
                seq += 1
                # the reference keeps the windows' RULE, the tail included: no
                # overlap, no tail (`text[-0:]` was the whole text, 2026-11-14)
                tail = text[-overlap_chars:] if overlap_chars else ""
                buf, size = ([tail, p], len(tail) + len(p)) if tail else ([p], len(p))
            else:
                buf.append(p)
                size += len(p)
        if buf:
            yield chunking._mk(uid, native_id, seq, chapter_title, "\n\n".join(buf), version)
            seq += 1


@pytest.mark.parametrize("max_chars, overlap", [(3920, 400), (4000, 0), (100, 99), (1000, 120), (7, 3)])
def test_the_index_hard_split_cuts_exactly_what_the_re_slicing_one_did(max_chars, overlap):
    """The rewrite changes how the windows are produced, never which: a
    one-paragraph book (CRLF text, verse, a dump's single line), paragraphs
    straddling the window, a text that is an exact multiple, an empty tail,
    overlap 0 and overlap at its maximum — identical ids, seqs and bytes."""
    import random
    rng = random.Random(max_chars * 1000 + overlap)
    words = ["lorem", "ipsum", "dolor", "sit", "amet", "x", "consectetur"]

    def para(n):
        return " ".join(rng.choice(words) for _ in range(n))
    books = [
        [("One paragraph", para(4000))],                                   # the hard-split path alone
        [("CRLF", "\r\n".join(para(30) for _ in range(300)))],             # no "\n\n": one paragraph
        [("Exact", "y" * (max_chars * 3))],                                 # an exact multiple
        [("Mixed", "\n\n".join(para(rng.randint(1, 900)) for _ in range(40)))],
        [("Short", "a"), ("Empty", "   \n\n  "), ("Tail", "z" * (max_chars + 1))],
    ]
    for book in books:
        want = ids(_reference_windows("g:7", 7, book, 3, max_chars, overlap))
        got = ids(chunking._fixed_windows("g:7", 7, book, 3, max_chars, overlap))
        assert got == want
        assert [c["tokens"] for c in _reference_windows("g:7", 7, book, 3, max_chars, overlap)] == \
            [c["tokens"] for c in chunking._fixed_windows("g:7", 7, book, 3, max_chars, overlap)]


def test_a_one_paragraph_book_costs_its_own_size_in_memory_not_a_copy_per_window():
    """The COST (scheduling.md pitfall 18). Re-slicing the remainder made a
    3.4 MB book without a blank line into ~1,000 fresh multi-MB strings —
    1.7 GB of allocations page-faulted in, 80 % system time on Perugia's
    bundle intake for 18 minutes per part. By index, the peak is the text
    plus one window: nothing is copied but the chunks themselves."""
    import tracemalloc
    text = "word " * 400_000                       # 2 MB, one paragraph

    def peak_of(windows):
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
            n = sum(1 for _ in windows("g:1", 1, [("B", text)], 1, 3920, 400))
            return n, tracemalloc.get_traced_memory()[1] - before
        finally:
            tracemalloc.stop()
    n_old, old = peak_of(_reference_windows)
    n_new, new = peak_of(chunking._fixed_windows)
    assert n_old == n_new > 500
    # `strip()` copies the paragraph once in both (the text ends in a space);
    # the old loop then held a second copy — the remainder — beside it at
    # every window, and churned a thousand of them; the new one holds the
    # paragraph and a window
    assert old > 1.8 * len(text), f"the oracle's peak {old:,} should show the remainder copy"
    assert new < 1.1 * len(text), f"peak {new:,} bytes over a {len(text):,}-byte paragraph"


def test_no_overlap_cuts_the_text_once_never_the_windows_before():
    """`text[-0:]` is the whole text: with overlap 0 each window used to carry
    every window before it — a 300 KB act became 30 MB of chunks, windows of
    40,189 tokens and the kernel's OOM kill (the LEMUR dry runs,
    2026-11-14). Now the windows partition the text."""
    body = "\n\n".join(f"Article {i}. " + "word " * 300 for i in range(200))
    cs = list(chunking.chunk_fixed("u:1", 1, [("", body)], 1, {"tokens": 512, "overlap": 0}))
    assert max(len(c["text"]) for c in cs) <= 512 * 4
    assert sum(len(c["text"]) for c in cs) <= len(body)
    assert "\n\n".join(c["text"] for c in cs) == "\n\n".join(p.strip() for p in body.split("\n\n")), \
        "every paragraph once, in order"


def test_an_overlap_still_carries_its_tail_byte_for_byte():
    """The fix changes nothing where there IS an overlap: each window still
    starts with the last `overlap * 4` characters of the one before."""
    cs = list(chunking.chunk_fixed("g:1", 1, BOOK, 1, {"tokens": 300, "overlap": 30}))
    firsts = [c for c in cs if c["chapter"] == "Chapter 1"]
    for a, b in zip(firsts, firsts[1:]):
        assert b["text"].startswith(a["text"][-120:])


# ---- the encoder's window, optional (operator, 2026-11-27, option B) ----------
# "Keep the max 4000 chars window as long as it fits the embedder ... If it
# does not fit, trim the size of the text to the previous paragraph or period"

import pytest as _pytest
from tokenizers import Tokenizer as _Tokenizer, models as _models, pre_tokenizers as _pre

from app import chunking as _chunking, layers as _layers
from app.plugins.wikidata import chunker as _wd

_SHA = "sha256:" + "ef" * 32


@_pytest.fixture
def words():
    """One token per whitespace-separated word."""
    tok = _Tokenizer(_models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = _pre.WhitespaceSplit()
    _wd.register(_SHA, tok)
    yield tok
    _wd._tokenizers.pop(_SHA, None)


def _fixed(chapters, **extra):
    params = {"tokens": 100, "overlap": 10, **extra}
    return list(_chunking.chunk_fixed("gutenberg:1", 1, chapters, version=1, params=params))


def test_a_window_that_fits_is_byte_for_byte_and_id_for_id_what_it_was(words):
    body = "\n\n".join(f"Paragraph {i} says a thing. It ends here." for i in range(60))
    before = _fixed([("C", body)])
    after = _fixed([("C", body)], tokenizer=_SHA, max_tokens=8175)
    assert after == before, "nothing over the window: every chunk, id and text, as before"


def test_a_window_over_the_limit_is_cut_back_to_its_last_sentence_and_nothing_is_lost(words):
    # one paragraph of 300 four-byte "words": 1,500 bytes, over a 70-token window
    sentence = "word " * 9 + "word."
    para = " ".join([sentence] * 30)
    before = _fixed([("C", para)], )
    after = _fixed([("C", para)], tokenizer=_SHA, max_tokens=70)
    assert all(len(c["text"].split()) <= 70 for c in after), "every piece within the window"
    assert "".join(c["text"].replace(" ", "") for c in after) == \
        "".join(c["text"].replace(" ", "") for c in before), "the pieces together are the windows' text"
    assert [c["seq"] for c in after] == list(range(len(after)))
    assert [c["id"] for c in after] == [f"gutenberg:1:{i}:1" for i in range(len(after))]


def test_only_the_window_over_the_limit_changes_and_the_rest_keep_their_text(words):
    fits = "\n\n".join(f"Short {i}." for i in range(5))
    long = " ".join(["word " * 9 + "word."] * 12)            # 120 words in one window
    before = _fixed([("A", fits), ("B", long), ("C", fits)])
    after = _fixed([("A", fits), ("B", long), ("C", fits)], tokenizer=_SHA, max_tokens=64)
    assert [c["text"] for c in after if c["chapter"] == "A"] == [c["text"] for c in before if c["chapter"] == "A"]
    assert [c["text"] for c in after if c["chapter"] == "C"] == [c["text"] for c in before if c["chapter"] == "C"]
    assert len([c for c in after if c["chapter"] == "B"]) > len([c for c in before if c["chapter"] == "B"])


def test_with_no_sentence_end_the_cut_falls_back_to_a_space_then_a_token_boundary(words):
    no_stop = " ".join(["word"] * 200)
    after = _fixed([("C", no_stop)], tokenizer=_SHA, max_tokens=64)
    assert all(len(c["text"].split()) <= 64 for c in after) and sum(len(c["text"].split()) for c in after) >= 200


def test_a_window_of_no_more_bytes_than_the_limit_is_never_counted(words, monkeypatch):
    calls = []
    real = _chunking._count
    monkeypatch.setattr(_chunking, "_count", lambda tok, t: calls.append(len(t)) or real(tok, t))
    _fixed([("C", "A short line. Another one.")], tokenizer=_SHA, max_tokens=8175)
    assert calls == [], "4,000 ASCII characters cannot exceed 8,175 tokens: nothing to count"


@_pytest.mark.parametrize("extra", [{"tokenizer": _SHA}, {"max_tokens": 8175}, {"tokenizer": "nope", "max_tokens": 8175},
                                    {"tokenizer": _SHA, "max_tokens": 10}])
def test_the_window_is_stated_whole_or_refused(words, extra):
    with _pytest.raises(_chunking.ChunkerError):
        _fixed([("C", "text.")], **extra)
    with _pytest.raises(_layers.LayerError):
        _layers.check_params({"tokens": 1000, "overlap": 120, **extra}, chunker=_chunking.FIXED, where="layer")


def test_the_cut_lands_on_the_last_sentence_end_that_fits(words):
    text = " ".join(["word " * 9 + "word."] * 30)          # 300 words, a stop every 10
    pieces = _chunking._fit(text, words, 64)
    assert all(len(p.split()) <= 64 for p in pieces)
    assert all(p.endswith(".") for p in pieces), "every cut at a sentence's end"
    assert [len(p.split()) for p in pieces[:-1]] == [60] * (len(pieces) - 1), "the LAST end that fits"
    assert " ".join(pieces).split() == text.split(), "nothing lost, nothing added"


def test_tibetan_stops_end_a_sentence(words):
    text = " ".join(["ཀ " * 9 + "ཀ །"] * 20)
    pieces = _chunking._fit(text, words, 64)
    assert all(p.endswith("།") for p in pieces[:-1]) and " ".join(pieces).split() == text.split()
