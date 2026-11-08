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
    assert out["text"].split("\n") == ["Legge 7 agosto 1990, n. 241", "Capo I › Art. 1", "Capo I › Art. 2",
                                         "Capo II › Art. 3", "Allegato A"], "an empty annex is still in the index"
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
