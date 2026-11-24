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


def test_a_number_is_never_split_in_an_article_form():
    """The rule the fix applies, kept by reading the forms: every digit run
    of an `after` form is possessive (`\\d++`), as `_ANUM`'s has been since a
    German sentence cost 424 us."""
    import re

    from app.refs import eurlex
    for f in eurlex._ALL:
        if f.after:
            assert not re.search(r"\\d\+(?!\+)|\\d\*(?!\+)", f.after), f"{f.lang}: a digit run that can be split"
