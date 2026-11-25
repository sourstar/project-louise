"""NO TEXT HOLDS A CITATION RESOLVER (app/refs; the node, 2026-11-24).

Every document's text goes through every resolver (`refs.RESOLVER`, the
text-index stage). On 2026-11-24 one Hungarian directive (32007L0019, 160 KB)
held the worker's pipeline thread inside ONE regex call for 21 minutes: the
GIL never left it, no collection embedded, the worker could not honour
SIGTERM. The article form written after an act let a number be split digit
by digit — `(?:\\d+[a-z]?\\.?(?:sep)?)*\\d+…cikk` — and the sentence " a 30340,
30401, 36640, 56800, 76815, 76866, 88640 és 93760 EGK-számú anyagokra" (eight
numbers, no article word) has more splits than the node has years. It was
reached from ANY act's number run, in any language.

A regex that does not come back cannot be stopped from inside its process,
so these tests run the resolvers in a CHILD with a deadline: a regression
fails with the case it stopped on, it does not hang the suite.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: what an act's citation is followed or preceded by, per resolver's language
OPENINGS = {
    "hu": "a 2007/19/EK irányelv ", "en": "Regulation (EU) 2016/679 ", "fi": "direktiivin 95/46/EY ",
    "lv": "Regulas (ES) Nr. 182/2011 ", "lt": "reglamento (ES) 2016/679 ", "et": "määruse (EL) nr 182/2011 ",
    "de": "§ 823 Abs. 1 BGB und Verordnung (EU) 2016/679 ",
    "it": "art. 5 della legge 7 agosto 1990, n. 241 e d.lgs. 196/2003 ",
    "fr": "l'article L. 121-1 du code de la consommation et la directive 95/46/CE ",
    "celex": "32016R0679 ", "none": "",
}
#: the node's sentence, as the directive writes it
NODE = ("megfelelő tömítést tartalmazó fedelek: a 2002/72/EK irányelv a 30340, 30401, 36640, 56800, 76815, "
        "76866, 88640 és 93760 EGK-számú anyagokra megállapított korlátozásoknak és előírásoknak nem "
        "megfelelő tömítést tartalmazó fedelek gyártása és a Közösségbe történő behozatala;")


#: the node's second sentence (23:21Z the same day): the Polish text of
#: Directive (EU) 2018/410, a list of articles BEFORE the act
NODE_PL = ("uprawnienia do przyjmowania aktów w\xa0odniesieniu do art. 3d ust. 3, art. 10 ust. 4, art. 10a ust. "
           "1 i\xa08, art. 10b ust. 5, art. 19 ust. 3, art. 22, art. 24 ust. 3, art. 24a ust. 1, art. 25a ust. 1 "
           "i\xa0art. 28c dyrektywy 2003/87/WE. Szczególnie ważne jest, aby w\xa0czasie prac przygotowawczych")
#: a list of articles before an act, as each language writes one: the
#: article with its paragraph, the conjunction before the last, the act
BEFORE = {
    "pl": ("art. {n} ust. {m}", "i", "dyrektywy 2003/87/WE"),
    "en": ("Article {n}({m})", "and", "of Directive 2003/87/EC"),
    "de": ("Artikel {n} Absatz {m}", "und", "der Richtlinie 2003/87/EG"),
    "fr": ("article {n}, paragraphe {m}", "et", "de la directive 2003/87/CE"),
    "it": ("articolo {n}, paragrafo {m}", "e", "della direttiva 2003/87/CE"),
    "es": ("artículo {n}, apartado {m}", "y", "de la Directiva 2003/87/CE"),
    "pt": ("artigo {n}.º, n.º {m}", "e", "da Diretiva 2003/87/CE"),
    "nl": ("artikel {n}, lid {m}", "en", "van Richtlijn 2003/87/EG"),
    "cs": ("čl. {n} odst. {m}", "a", "směrnice 2003/87/ES"),
    "sk": ("článku {n} ods. {m}", "a", "smernice 2003/87/ES"),
    "hr": ("članka {n}. stavka {m}.", "i", "Direktive 2003/87/EZ"),
    "sl": ("člena {n}(m{m})", "in", "Direktive 2003/87/ES"),
    "ro": ("articolul {n} alineatul ({m})", "și", "din Directiva 2003/87/CE"),
    "sv": ("artikel {n}.{m}", "och", "i direktiv 2003/87/EG"),
    "da": ("artikel {n}, stk. {m}", "og", "i direktiv 2003/87/EF"),
    "bg": ("член {n}, параграф {m}", "и", "от Директива 2003/87/ЕО"),
    "el": ("άρθρο {n} παράγραφος {m}", "και", "της οδηγίας 2003/87/ΕΚ"),
    "mt": ("Artikolu {n}({m})", "u", "tad-Direttiva 2003/87/KE"),
}


def before(n: int):
    """(name, text): a list of `n` articles before an act, per language —
    the shape that was read in as many ways as languages share its article
    word, to the power of its length."""
    for lang, (item, conj, act) in BEFORE.items():
        items = [item.format(n=3 + 2 * i, m=1 + i % 9) for i in range(n)]
        yield f"{lang}, {n} articles", ", ".join(items[:-1]) + f" {conj} {items[-1]} {act}"


def hostile(n: int):
    """(name, text): the shapes a pattern with a loop in it can be made to
    try every split of — lists of numbers with and without their separators,
    dots, letters and calls, long runs of one character or one word."""
    nums = [str(30340 + 61 * i) for i in range(n + 1)]
    yield "digits, és", "a " + ", ".join(nums[:n]) + " és " + nums[n] + " EGK-számú anyagokra"
    for conj in ("and", "ja", "un", "ir", "et", "e", "und", "y", "i", "a", "vagy", "tai", "või", "arba", "bei"):
        yield f"digits {conj}", f" {conj} ".join(nums[:n]) + " xyz"
    yield "dotted", ", ".join(f"{i}." for i in range(1, n + 1)) + " xyz"
    yield "lettered", ", ".join(f"{i}a" for i in range(1, n + 1)) + " xyz"
    yield "calls", " ".join(f"({i})" for i in range(1, n + 1)) + " xyz"
    yield "slashes", ", ".join(f"{i}/{2000 + i % 20}" for i in range(1, n + 1)) + " xyz"
    yield "slashes hu", " és ".join(f"a {i}/{2000 + i % 20}/EK" for i in range(1, n + 1)) + " rendeletet"
    yield "long digits", "1" * (n * 20) + " xyz"
    yield "spaces", " " * (n * 100) + "2017/123 xyz"
    yield "newlines", " \n" * (n * 50) + "2017/123 xyz"
    yield "commas", ", " * (n * 20) + "2017/123"
    yield "paragraphs", "§ " * (n * 10) + "5 BGB"
    yield "articles", "art. " * (n * 10) + "5"
    yield "numeros", "n. " * (n * 10) + "241"
    yield "parens", "(" * (n * 5) + "EU" + ")" * (n * 5) + " 2016/679"
    yield "orgs", "(EU, Euratom, " * (n * 3) + ") 2016/679"
    yield "abs", "Abs. 1 " * (n * 10) + "BGB"
    yield "commi", "comma 1, " * (n * 10) + "della legge n. 241 del 1990"


def cases(n: int = 64):
    yield "the node's sentence", NODE
    yield "the node's second sentence", NODE_PL
    for k in (5, 9, 17, n):
        for name, text in before(k):
            yield f"before / {name}", text
    for oname, opening in OPENINGS.items():
        for tname, tail in hostile(n):
            yield f"{oname} / {tname} / after", opening + tail
            yield f"{oname} / {tname} / before", tail + " " + opening


def _child() -> None:
    """Every case through every resolver, each named BEFORE it runs: what the
    parent reads of a child it had to kill ends on the case that held it."""
    import time

    from app import refs

    class Nothing:
        def get(self, *a, **k):
            return None

        def rows(self, *a, **k):
            return []
    for name, text in cases():
        print(json.dumps({"case": name}), flush=True)
        t0 = time.monotonic()
        refs.RESOLVER.resolve(text, Nothing())
        print(json.dumps({"case": name, "s": time.monotonic() - t0}), flush=True)


def _run(code: str, limit_s: float) -> tuple[list[dict], bool]:
    """(the child's lines, whether it finished) — killed at `limit_s`."""
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), stdout=subprocess.PIPE, text=True)
    try:
        out, _ = p.communicate(timeout=limit_s)
        done = p.returncode == 0
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        done = False
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")], done


def test_no_text_holds_a_citation_resolver():
    """Some 600 hostile texts through every resolver: all of them back in a
    minute (0.1 s here), none over a second. Before the fix the first
    Hungarian list never came back."""
    lines, done = _run("from tests import test_refs_hostile_text as t; t._child()", 60.0)
    assert lines, "the child said nothing"
    assert done, f"a resolver did not come back from: {lines[-1]['case']}"
    timed = [row for row in lines if "s" in row]
    assert len(timed) == len(list(cases())) > 500
    slowest = max(timed, key=lambda row: row["s"])
    assert slowest["s"] < 1.0, f"{slowest['case']} took {slowest['s']:.2f} s"


def test_the_nodes_sentence_names_its_directive_and_no_article():
    """The eight numbers are substances, not articles: the act is named, with
    no article, at once."""
    from app.refs import eurlex
    code = ("import json, time; from app.refs import eurlex; from tests import test_refs_hostile_text as t; "
            "t0 = time.monotonic(); got = eurlex.citations(t.NODE); "
            "print(json.dumps({'got': got, 's': time.monotonic() - t0}), flush=True)")
    lines, done = _run(code, 20.0)
    assert done and lines, "the node's sentence held the resolver"
    assert lines[-1]["got"] == [{"celex": "32002L0072", "art": None}] and lines[-1]["s"] < 1.0
    # and the lists that ARE articles are read as before, each number whole
    assert eurlex.citations("a 182/2011/EU rendelet 3., 4. és 15. cikkét") == [
        {"celex": "32011R0182", "art": "3., 4. és 15. cikkét"}]
    assert eurlex.citations("asetuksen (EU) N:o 182/2011 5, 6 ja 17 artiklaa") == [
        {"celex": "32011R0182", "art": "5, 6 ja 17 artiklaa"}]
    assert eurlex.citations("Regulas (ES) Nr. 182/2011 5., 6. un 17. pantu") == [
        {"celex": "32011R0182", "art": "5., 6. un 17. pantu"}]


def test_a_long_list_of_articles_before_an_act_is_read_at_once():
    """The node, 2026-11-24 23:21Z: the Polish text of Directive (EU)
    2018/410 did not come back from a process of its own in 130 s. Its list
    is too long for the sixteen tokens an article list may have, and "art."
    was an alternative of twelve languages' forms: the failing match tried
    the same reading 12 to the power of the list's articles. Five articles
    took 94 s; now a millisecond, and the act is named as before."""
    from app.refs import eurlex
    code = ("import json, time; from app.refs import eurlex; from tests import test_refs_hostile_text as t; "
            "t0 = time.monotonic(); got = eurlex.citations(t.NODE_PL); "
            "print(json.dumps({'got': got, 's': time.monotonic() - t0}), flush=True)")
    lines, done = _run(code, 20.0)
    assert done and lines, "the node's second sentence held the resolver"
    assert lines[-1]["s"] < 1.0
    assert [c["celex"] for c in lines[-1]["got"]] == ["32003L0087"], lines[-1]["got"]
    # a list short enough is still read whole, from its first article
    assert eurlex.citations("art. 19 ust. 3, art. 22 i art. 28c dyrektywy 2003/87/WE") == [
        {"celex": "32003L0087", "art": "art. 19 ust. 3, art. 22 i art. 28c"}]


def _alternatives(source: str) -> list[str]:
    """`source` cut at its top-level bars."""
    out, depth, cur, i = [], 0, "", 0
    while i < len(source):
        c = source[i]
        if c == "\\":
            cur += source[i:i + 2]
            i += 2
            continue
        if c == "[":
            j = source.index("]", i + 2)
            cur += source[i:j + 1]
            i = j + 1
            continue
        depth += (c == "(") - (c == ")")
        if c == "|" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    return out + [cur]


def test_a_union_of_the_languages_forms_reads_each_word_once_and_what_it_read():
    """`_union`: the forms are per language and the languages share words.
    Each reading is listed once, in the order the engine tried it, so the
    alternation reads the strings it read — held here against the forms
    joined as they are written, on every reading with every tail."""
    import re

    from app.refs import eurlex
    assert eurlex._readings(r"art(?:icle)?s?\.?") == ["articles.", "articles", "article.", "article",
                                                      "arts.", "arts", "art.", "art"]
    assert eurlex._readings(r"alin[ée]as?") == ["alinéas", "alinéa", "alineas", "alinea"]
    assert eurlex._readings("x??y") == ["y", "xy"], "a lazy option: without it first"
    for other in (r"a\s+b", r"a+", r"(?P<n>a)", r"a{2}", r"a.b", r"[a-c]"):
        assert eurlex._readings(other) is None, other
    tails = ("", ".", " 5", "s", "x", "\xa05")
    for field, forms in (("art", eurlex._ALL), ("sub", eurlex._FORMS), ("conj", eurlex._FORMS),
                         ("range", eurlex._FORMS), ("conn", eurlex._FORMS), ("link", eurlex._FORMS),
                         ("pre", eurlex._FORMS), ("opener", eurlex._FORMS), ("glued", eurlex._FORMS),
                         ("num_first", eurlex._ALL)):
        written = "|".join(f"(?:{getattr(f, field)})" for f in forms if getattr(f, field))
        once = eurlex._union(field, forms)
        alts = _alternatives(once)
        assert len(alts) == len(set(alts)), field
        old, new = re.compile(f"(?:{written})", re.I), re.compile(f"(?:{once})", re.I)
        words = [w for f in forms if getattr(f, field) for w in (eurlex._readings(getattr(f, field)) or [])]
        assert words or field in ("num_first",), field
        for w in dict.fromkeys(words):
            for tail in tails:
                text = w + tail
                a, b = old.match(text), new.match(text)
                assert (a and a.end()) == (b and b.end()), (field, text)
                # every end either can reach: the same strings read, not only the first
                for q in range(1, len(text) + 1):
                    assert bool(old.fullmatch(text, 0, q)) == bool(new.fullmatch(text, 0, q)), (field, text, q)
    assert sum(1 for f in eurlex._ALL if f.art and (eurlex._readings(f.art) or []).count("art.")) >= 10, \
        "the word the node's list repeats is many languages'"
    assert _alternatives(eurlex._ART_WORD).count(re.escape("art.")) == 1


def test_a_number_is_never_split_in_an_article_form():
    """The rule the fix applies, kept by reading the forms: every digit run
    of an `after` form is possessive (`\\d++`), as `_ANUM`'s has been since a
    German sentence cost 424 us."""
    import re

    from app.refs import eurlex
    for f in eurlex._ALL:
        if f.after:
            assert not re.search(r"\\d\+(?!\+)|\\d\*(?!\+)", f.after), f"{f.lang}: a digit run that can be split"
