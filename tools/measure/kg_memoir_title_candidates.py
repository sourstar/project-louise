"""wiki-gutenberg: memoir candidates by title word, for the operator to mark.

    python -m tools.measure.kg_memoir_title_candidates pg_catalog.csv --lang fr --out DIR
        [--decided docs/notes/wiki-gutenberg-memoir-titles-it.toml]

A candidate is a text in the language whose TITLE holds one of the language's
memoir words and which the subject rule (kg_scope_count.is_memoir) does not
already put in the memoir group. Each candidate gets a CATEGORY from its
record alone (subjects, LoCC, title) and the suggestion the operator's
decisions imply (2026-11-18: memoirs, biographies, first-person chronicles
and eyewitness accounts, travel included, YES; fiction and history or
criticism NO). Nothing is read from the books; the operator decides.

Writes <DIR>/memoir-candidates-<lang>.csv (every row, an empty `decision`
column) and prints the category counts.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import kg_scope_count as scope  # noqa: E402

WORDS: dict[str, str] = {
    "it": r"memorie|memoria|ricordi|ricordo|rimembranze|diario|taccuino|noterelle|lettere|epistolario|carteggio|autobiografia|vita di|la mia vita|confessioni|mie prigioni|cronaca|cronache|viaggio|viaggi|impressioni",
    "en": r"memoirs?|recollections?|reminiscences?|diary|diaries|journal|letters|correspondence|autobiography|life of|my life|confessions?|chronicles?|narrative|travels?|voyages?|journey|tour|personal experiences?|eyewitness|eye-witness",
    "fr": r"mémoires|souvenirs?|journal|lettres|correspondance|autobiographie|vie de|ma vie|confessions?|chroniques?|récits?|voyages?|relation",
    "de": r"erinnerungen|lebenserinnerungen|denkwürdigkeiten|memoiren|tagebuch|tagebücher|briefe|briefwechsel|autobiographie|selbstbiographie|leben|bekenntnisse|chronik|reisen?|reisebriefe",
    "es": r"memorias|recuerdos?|diario|cartas|correspondencia|autobiografía|vida de|mi vida|confesiones|crónicas?|relación|viajes?",
}

#: A personal-name heading, "Surname, Forename, 1807-1882" (LCSH form).
PERSON = re.compile(r"^[^,;]+, [^,;]+, (ca\. )?\d{3,4}\??-(\d{3,4}\??)?")
#: An issue of a periodical: an issue number, or a periodical's own wording. A
#: volume number alone is not one (multi-volume memoirs: "Memoirs of the Reign of
#: King George the Third, Volume 1").
PERIODICAL = re.compile(r"\b(no\. ?\d+|number \d+|magazine|weekly|monthly|quarterly|annual report|proceedings|bulletin)\b", re.I)
CRITICISM = ("History and criticism", "Historiography", "Study and teaching", "Criticism and interpretation")
TRAVEL = ("Description and travel", "Voyages and travels", "Travel", "Discovery and exploration")
WITNESS = ("Personal narratives", "Sources", "Captivities", "Prisoners", "Pioneers", "Frontier and pioneer life")

#: category -> suggestion
SUGGEST = {
    "fiction, poetry or drama": "no",
    "periodical": "no",
    "history or criticism": "no",
    "travel account": "yes",
    "eyewitness or source": "yes",
    "about a person": "yes",
    "review": "?",
}


@dataclass(frozen=True)
class Candidate:
    ebook: int
    title: str
    author: str
    locc: str
    subjects: str
    word: str
    now: str
    category: str

    @property
    def suggestion(self) -> str:
        return SUGGEST[self.category]


def categorize(title: str, subs: list[str], locc: str) -> str:
    """The category, from the record alone. The first rule that matches wins."""
    if scope.is_out(subs, locc):
        return "fiction, poetry or drama"
    if PERIODICAL.search(title) or any("Periodicals" in s for s in subs):
        return "periodical"
    if any(s.split("--")[-1].strip() in CRITICISM for s in subs):
        return "history or criticism"
    if any(t in s for s in subs for t in TRAVEL) or any(c.strip().startswith("G") for c in locc.split(";")):
        return "travel account"
    if any(w in s for s in subs for w in WITNESS):
        return "eyewitness or source"
    if any(PERSON.match(s) for s in subs):
        return "about a person"
    return "review"


def candidates(catalog: Path, lang: str, decided: set[int] = frozenset()) -> list[Candidate]:
    rx = re.compile(r"\b(" + WORDS[lang] + r")\b", re.I)
    out: list[Candidate] = []
    with catalog.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["Type"] != "Text" or r["Language"].split(";")[0].strip() != lang:
                continue
            ebook = int(r["Text#"])
            title = " ".join(r["Title"].split())
            m = rx.search(title)
            if not m or ebook in decided:
                continue
            subs, locc = scope.subjects(r), r["LoCC"]
            if scope.is_memoir(subs, locc):
                continue
            now = "out" if scope.is_out(subs, locc) else ("history" if scope.is_history(locc) else "not in scope")
            out.append(Candidate(ebook, title, r["Authors"].split(";")[0].strip(), locc,
                                 "; ".join(subs), m.group(1).lower(), now, categorize(title, subs, locc)))
    order = list(SUGGEST)
    out.sort(key=lambda c: (order.index(c.category), c.ebook))
    return out


def decided_ebooks(paths: list[Path]) -> set[int]:
    got: set[int] = set()
    for p in paths:
        d = tomllib.loads(p.read_text(encoding="utf-8"))
        got |= set(d.get("yes", [])) | set(d.get("no", []))
    return got


def write_csv(rows: list[Candidate], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ebook", "url", "title", "author", "locc", "subjects", "word", "now", "category", "suggestion", "decision"])
        for c in rows:
            w.writerow([c.ebook, f"https://www.gutenberg.org/ebooks/{c.ebook}", c.title, c.author, c.locc,
                        c.subjects, c.word, c.now, c.category, c.suggestion, ""])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--lang", required=True, choices=sorted(WORDS))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--decided", type=Path, action="append", default=[])
    a = ap.parse_args(argv)
    rows = candidates(a.catalog, a.lang, decided_ebooks(a.decided))
    a.out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, a.out / f"memoir-candidates-{a.lang}.csv")
    for cat, n in Counter(c.category for c in rows).items():
        print(a.lang, cat, n, sep="\t")


if __name__ == "__main__":
    main()
