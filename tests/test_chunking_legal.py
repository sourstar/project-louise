"""The `legal_article` chunker (app/chunking_legal.py; legal-chunking-retrieval.md):
one chunk per article, a longer one split at its own paragraph and point
numbering in the four systems, then sentences, then token windows — every
piece within `max_tokens` of the pinned tokenizer, the header counted, and
the pieces together the whole text: nothing truncated, nothing dropped."""
import re

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app import chunking, chunking_legal as cl, layers
from app.plugins.wikidata import chunker as wd

SHA = "sha256:" + "cd" * 32


@pytest.fixture(autouse=True)
def word_tokenizer():
    """One token per whitespace-separated word, offsets kept."""
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    wd.register(SHA, tok)
    yield tok
    wd._tokenizers.pop(SHA, None)


def _chunks(sections, max_tokens=64, header=False, outline=False, title=None):
    params = {"tokenizer": SHA, "max_tokens": max_tokens, "header": header, "outline": outline}
    return list(cl.chunk_legal_article("legi:1", 1, sections, params=params,
                                       title=title if title is not None else ("T" if header or outline else None)))


def _words(text):
    return re.findall(r"\S+", text)


def _assert_lossless(sections, chunks, header=False):
    """Every word of every section, in order, exactly once (headers aside)."""
    got = []
    for c in chunks:
        text = c["text"]
        if header and c["chapter"]:
            assert text.startswith(c["chapter"] + "\n")
            text = text[len(c["chapter"]) + 1:]
        got += _words(text)
    assert got == [w for _l, b in sections for w in _words(b)]


def _para(mark, n):
    tag = re.sub(r"\W", "", mark)
    return f"{mark} " + " ".join(f"w{tag}x{i}" for i in range(n - len(mark.split()) + 1))


def test_a_short_article_is_one_chunk():
    s = [("Art. 1", "Le présent code s'applique.")]
    (c,) = _chunks(s)
    assert c["text"] == "Le présent code s'applique." and c["chapter"] == "Art. 1"
    assert c["id"] == f"legi:1:0:{cl.CHUNK_VERSION}"


@pytest.mark.parametrize("marks", [
    ["1.", "2.", "2-bis.", "3."],                # Italian commi / EU paragraphs
    ["(1)", "(2)", "(2a)", "(3)"],               # German Absätze
    ["I. —", "II. –", "III. —", "IV. -"],         # French numbered parts
])
def test_a_long_article_is_split_at_its_own_paragraphs(marks):
    body = "\n".join(_para(m, 30) for m in marks)
    s = [("§ 5", body)]
    chunks = _chunks(s, max_tokens=64)
    assert all(len(_words(c["text"])) <= 64 for c in chunks)
    assert len(chunks) == 2, "two paragraphs of ~31 words packed into each 64-token piece"
    for c in chunks:
        first = c["text"].split("\n", 1)[0]
        assert any(first.startswith(m) for m in marks), f"a piece starts at a paragraph: {first!r}"
    _assert_lossless(s, chunks)


def test_a_paragraph_past_the_window_is_split_at_its_points_then_sentences_then_windows():
    points = "\n".join([
        "(1) Ordnungswidrig handelt, wer",
        "1. " + " ".join(f"a{i}" for i in range(20)) + ",",
        "a) " + " ".join(f"b{i}" for i in range(20)) + ",",
        "b) " + " ".join(f"c{i}" for i in range(20)) + "."])
    sentences = "(2) " + " ".join(f"Satz{i} hat Wörter. Und weiter, siehe Abs. 3 und Nr. 2 dort." for i in range(10))
    monster = "(3) " + " ".join(f"x{i}" for i in range(500))
    s = [("§ 29 BtMG", "\n".join([points, sentences, monster]))]
    chunks = _chunks(s, max_tokens=40)
    assert all(len(_words(c["text"])) <= 40 for c in chunks), "within the window, always"
    _assert_lossless(s, chunks)
    texts = [c["text"] for c in chunks]
    assert any(t.startswith("a) ") for t in texts), "a point starts a piece"
    assert not any(t.startswith("3 ") or t.startswith("2 dort") for t in texts), \
        "never split after the abbreviation of a reference (Abs., Nr.)"


def test_the_header_is_the_title_and_the_label_on_every_piece_and_counted_in_the_window():
    """Piece 1 (operator, 2026-11-08): "<title> › <label>" at the head of each
    piece — G-DSR's headings in the text the encoder reads."""
    body = "\n".join(_para(f"{i}.", 25) for i in range(1, 6))
    s = [("Libro IV › Titolo IX › Art. 2043 — (Risarcimento per fatto illecito)", body)]
    title = "REGIO DECRETO 16 marzo 1942, n. 262 — Codice civile"
    chunks = _chunks(s, max_tokens=60, header=True, title=title)
    head = f"{title} › {s[0][0]}\n"
    assert len(chunks) > 1
    assert all(c["text"].startswith(head) for c in chunks)
    assert all(len(_words(c["text"])) <= 60 for c in chunks), "the header inside the window"
    got = [w for c in chunks for w in _words(c["text"][len(head):])]
    assert got == _words(body), "every word of the article, once, in order"


def test_the_outline_is_one_more_chunk_with_the_title_and_every_label():
    """Piece 2: the act's own index, after its articles, token-bounded."""
    s = [("Capo I › Art. 1", "uno"), ("Capo I › Art. 2", "due"), ("Capo II › Art. 3", "tre"),
         ("Allegato A", "")]
    chunks = _chunks(s, outline=True, title="Legge 7 agosto 1990, n. 241")
    *articles, out = chunks
    assert [c["chapter"] for c in articles] == ["Capo I › Art. 1", "Capo I › Art. 2", "Capo II › Art. 3"]
    assert out["chapter"] == cl.OUTLINE and out["seq"] == 3, "after the articles: their seqs stay put"
    assert out["text"].split("\n") == ["Legge 7 agosto 1990, n. 241", "Capo I", "  Art. 1", "  Art. 2",
                                         "Capo II", "  Art. 3", "Allegato A"], \
        "a table of contents (v2); an empty annex is still in the index"
    long = [(f"Art. {i}", "x") for i in range(200)]
    outs = [c for c in _chunks(long, max_tokens=40, outline=True, title="Codice") if c["chapter"] == cl.OUTLINE]
    assert len(outs) > 1 and all(c["text"].startswith("Codice\n") for c in outs)
    assert all(len(_words(c["text"])) <= 40 for c in outs)
    assert [c for c in _chunks([("Art. 1", "solo")], outline=True) if c["chapter"] == cl.OUTLINE] == [], \
        "a record of one section (a LEGI article) has no outline of its own"


def test_a_layer_writing_the_title_refuses_a_caller_that_passed_none():
    with pytest.raises(cl.LegalChunkerError, match="passed none"):
        list(cl.chunk_legal_article("legi:1", 1, [("Art. 1", "x")],
                                    params={"tokenizer": SHA, "max_tokens": 64, "header": True}))
    assert len(list(cl.chunk_legal_article("legi:1", 1, [("Art. 1", "x")],
                                           params={"tokenizer": SHA, "max_tokens": 64}))) == 1, \
        "a layer that writes no title needs none"


def test_every_caller_that_cuts_passes_the_record_s_title():
    """Invariant 3: the cleaned text is [title, sections]; a caller that did
    not pass the title would cut a legal_article layer otherwise (or not at
    all) — so every call of chunks_for_document names `title=`."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    missing = []
    for p in [*root.joinpath("app").rglob("*.py"), *root.joinpath("tools").rglob("*.py")]:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name == "chunks_for_document" and not any(k.arg == "title" for k in n.keywords):
                    missing.append(f"{p.relative_to(root)}:{n.lineno}")
                # a bound chunker called directly: `chunker_at(i)(…)`, `chunker_for_document(…)(…)`
                inner = f if isinstance(f, ast.Call) else None
                iname = (inner.func.attr if inner is not None and isinstance(inner.func, ast.Attribute)
                         else getattr(getattr(inner, "func", None), "id", None))
                if iname in ("chunker_at", "chunker_for_document") and \
                        not any(k.arg == "title" for k in n.keywords):
                    missing.append(f"{p.relative_to(root)}:{n.lineno}")
    assert not missing, f"cut without the record's title: {missing}"


def test_sections_run_one_sequence_and_an_empty_section_gives_none():
    s = [("Art. 1", "uno"), ("Art. 2", "   "), ("Anlage 1", "zwei drei")]
    chunks = _chunks(s)
    assert [c["seq"] for c in chunks] == [0, 1] and [c["chapter"] for c in chunks] == ["Art. 1", "Anlage 1"]


def test_the_cut_is_a_pure_function_of_text_and_params():
    body = "\n".join(_para(f"({i})", 33) for i in range(1, 9))
    a, b = _chunks([("§ 1", body)], max_tokens=50), _chunks([("§ 1", body)], max_tokens=50)
    assert [c["text"] for c in a] == [c["text"] for c in b]


def test_the_layer_names_its_tokenizer_and_a_usable_window():
    layers.check_params({"tokenizer": SHA, "max_tokens": 8100, "header": True},
                        chunker=chunking.LEGAL_ARTICLE, where="layer 0")
    for bad in ({"max_tokens": 8100}, {"tokenizer": "nope", "max_tokens": 8100},
                {"tokenizer": SHA, "max_tokens": 8}, {"tokenizer": SHA, "max_tokens": 8100, "header": "yes"},
                {"tokenizer": SHA, "max_tokens": 8100, "outline": 1},
                {"tokenizer": SHA, "max_tokens": 8100, "overlap": 10}):
        with pytest.raises(layers.LayerError):
            layers.check_params(bad, chunker=chunking.LEGAL_ARTICLE, where="layer 0")


def test_it_is_registered_with_its_own_version():
    assert chunking.chunker_name(chunking.LEGAL_ARTICLE) == chunking.LEGAL_ARTICLE
    assert chunking.chunk_version_for(chunking.LEGAL_ARTICLE) == cl.CHUNK_VERSION
    assert chunking.chunk_version_for(chunking.AKN_ARTICLE) == chunking.AKN_CHUNK_VERSION, \
        "Normattiva's chunker is untouched"


# ---- version 2 (2026-11-08): the header said once, the outline a table of contents

_LAW = "LEGGE 31 ottobre 2003, n. 306 — Legge comunitaria 2003."
_SECTIONS = [("CAPO I — DISPOSIZIONI GENERALI › Art. 1. — (Delega al Governo).",
              "Art. 1. — (Delega al Governo).\n1. Il Governo e' delegato ad adottare decreti."),
             ("CAPO I — DISPOSIZIONI GENERALI › Art. 2. — (Principi).",
              "Art. 2. — (Principi).\n1. Salvi gli specifici principi."),
             ("CAPO II — ALTRE DISPOSIZIONI › Art. 3. — (Abrogazioni).",
              "Art. 3. — (Abrogazioni).")]


def _cut(version):
    from app import chunking_legal as cl
    return list(cl.chunk_legal_article("normattiva:1", 1, _SECTIONS, version,
                                       {**cl.SHARED_LAYER["chunker"]["params"]}, title=_LAW))


def test_v2_says_the_article_line_once_and_keeps_a_body_that_is_only_that_line():
    first, second, third = _cut(2)[:3]
    assert first["text"] == (f"{_LAW} › CAPO I — DISPOSIZIONI GENERALI › Art. 1. — (Delega al Governo).\n"
                             "1. Il Governo e' delegato ad adottare decreti.")
    assert first["text"].count("Art. 1. —") == 1
    assert second["text"].endswith("\n1. Salvi gli specifici principi.")
    assert third["text"].endswith("\nArt. 3. — (Abrogazioni)."), "a body that is its line alone is kept"


def test_v2_outline_is_a_table_of_contents():
    outline = _cut(2)[-1]
    assert outline["chapter"] == "outline"
    assert outline["text"] == (f"{_LAW}\n"
                               "CAPO I — DISPOSIZIONI GENERALI\n"
                               "  Art. 1. — (Delega al Governo).\n"
                               "  Art. 2. — (Principi).\n"
                               "CAPO II — ALTRE DISPOSIZIONI\n"
                               "  Art. 3. — (Abrogazioni).")


def test_v1_cuts_as_it_always_did():
    """Invariant 3: a layer pinned to version 1 gives the same chunks."""
    first, outline = _cut(1)[0], _cut(1)[-1]
    assert first["text"] == (f"{_LAW} › CAPO I — DISPOSIZIONI GENERALI › Art. 1. — (Delega al Governo).\n"
                             "Art. 1. — (Delega al Governo).\n1. Il Governo e' delegato ad adottare decreti.")
    assert outline["text"] == _LAW + "\n" + "\n".join(l for l, _b in _SECTIONS)
    assert first["id"].endswith(":1") and _cut(2)[0]["id"].endswith(":2")


def test_an_unknown_version_is_refused():
    from app import chunking_legal as cl
    with pytest.raises(cl.LegalChunkerError, match="versions"):
        _cut(4)


# ---- version 3 (2026-11-08): units packed linearly, every piece held to the bound ---------------

@pytest.fixture(scope="module")
def bpe():
    """A byte-level BPE trained on Normattiva fixture text: its counts do NOT
    add across a join (a separator merges into the next word's token), which
    is the case v3's exact check exists for."""
    from pathlib import Path
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    from app.plugins.normattiva import akn, rendition
    texts = []
    for f in sorted(Path(__file__).parent.joinpath("fixtures", "normattiva").glob("*.xml")):
        xml, _ = akn.decode_member(f.read_bytes())
        texts += [b for _l, b in rendition.sections(akn.parse(xml)) if b]
    tok = Tokenizer(models.BPE(unk_token=None))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.train_from_iterator(texts * 5, trainers.BpeTrainer(vocab_size=4000,
                                                           initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    return tok, texts


class _Counting:
    def __init__(self, tok):
        self.tok, self.chars = tok, 0

    def encode(self, text, add_special_tokens=False):
        self.chars += len(text)
        return self.tok.encode(text, add_special_tokens=add_special_tokens)


def _long(texts, n, sep):
    import random
    rnd = random.Random(7)
    if sep == "\n":
        return "\n".join(f"{i}. " + " ".join(rnd.choice(texts).split()[:40]) for i in range(1, n + 1))
    return " ".join(" ".join(rnd.choice(texts).split()[:20]).rstrip(".") + "." for _ in range(n))


@pytest.mark.parametrize("sep", ["\n", " "])
def test_v3_holds_every_piece_to_the_bound_and_keeps_the_whole_text(bpe, sep):
    tok, texts = bpe
    text = _long(texts, 300, sep)
    pieces = cl.split(tok, text, 400, version=3)
    assert len(pieces) > 5
    assert max(len(tok.encode(p, add_special_tokens=False).ids) for p in pieces) <= 400, "never past the window"
    assert sep.join(pieces) == text, "joined, the pieces are the text"


def test_v3_is_linear_where_v2_re_encodes_the_growing_piece(bpe):
    tok, texts = bpe
    text = _long(texts, 1500, " ")
    seen = {}
    for v in (2, 3):
        counting = _Counting(tok)
        cl.split(counting, text, 1000, version=v)
        seen[v] = counting.chars / len(text)
    assert seen[3] < 3, f"v3 encodes about twice the text, not {seen[3]:.1f} times"
    assert seen[2] > 10 * seen[3], f"v2 re-encodes the growing piece ({seen[2]:.1f} times the text)"


def test_v3_with_counts_that_add_cuts_as_v2_did(bpe):
    """Where a join adds no token (the word tokenizer every legal test uses),
    the sum IS the joined count, so v3's pieces are v2's."""
    _tok, texts = bpe
    from app.plugins.wikidata import chunker as wd
    word = wd.tokenizer_for(SHA)
    text = _long(texts, 200, "\n")
    assert cl.split(word, text, 300, version=3) == cl.split(word, text, 300, version=2)


def test_the_shared_layer_is_v3():
    assert cl.CHUNK_VERSION == 3 and cl.SHARED_LAYER["chunker"]["version"] == 3
