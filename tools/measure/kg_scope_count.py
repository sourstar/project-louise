"""Count history (LoCC C, D, E, F) + memoir books in pg_catalog.csv, fiction/poetry/drama out.

Rule: wiki-gutenberg-amendments.md "Which books" (operator 2026-11-08), narrowed to history + memoirs.
"""
import csv, re, sys, collections

IN_WORDS = ("Biography", "Autobiography", "Memoirs", "Diaries", "Correspondence", "Personal narratives")
EXEMPT_LAST = ("History and criticism", "Technique", "Study and teaching", "Bibliography", "Theory")


def subjects(row):
    return [s.strip() for s in row["Subjects"].split(";") if s.strip()]


#: Genre headings for fiction that do not say "fiction" (found 2026-11-18 by
#: tools/measure/pg_volume_consistency.py: "Bildungsromans", "Love stories",
#: "Sea stories", "Fairy tales", "-- Legends"). Read on the LAST part of a heading.
FICTION_GENRES = re.compile(
    r"\b(stories|bildungsromans|romances|novels|tales|fables|legends|picaresque literature)\b", re.I)


def _last(heading):
    return heading.split("--")[-1].strip()


def is_out(subs, locc):
    if any(c.strip().startswith("PZ") for c in locc.split(";")):
        return True
    for s in subs:
        if _last(s) in EXEMPT_LAST:
            continue
        low = s.lower()
        if "fiction" in low or "poetry" in low or "drama" in low:
            return True
        if FICTION_GENRES.search(_last(s)):
            return True
    return False


def _is_fiction_form(heading):
    """A heading whose last part is a fiction form: "Diaries -- Fiction"."""
    low = _last(heading).lower()
    return "fiction" in low or FICTION_GENRES.search(low) is not None


def is_juvenile(subs, shelves=""):
    """A book for children: a "Juvenile ..." subject, or a children's bookshelf
    (operator, 2026-11-18: "No biographies for children")."""
    return any("juvenile" in s.lower() for s in subs) or "children" in shelves.lower()


def is_memoir(subs, locc, shelves=""):
    if is_juvenile(subs, shelves):
        return False
    if any(c.strip().startswith("CT") for c in locc.split(";")):
        return True
    # A memoir word inside a fiction heading ("Diaries -- Fiction") is not a memoir.
    return any(w.lower() in s.lower() for s in subs if not _is_fiction_form(s) for w in IN_WORDS)


def is_history(locc):
    return any(c.strip()[:1] in "CDEF" for c in locc.split(";") if c.strip())


def main(path):
    counts = collections.Counter()
    langs = collections.Counter()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Type"] != "Text":
                continue
            subs, locc = subjects(row), row["LoCC"]
            memoir = is_memoir(subs, locc, row["Bookshelves"])
            if is_out(subs, locc) and not memoir:
                continue
            # Children's books are out of the history scope too (operator, 2026-11-18).
            hist = is_history(locc) and not is_juvenile(subs, row["Bookshelves"])
            if not (hist or memoir):
                continue
            lang = row["Language"].split(";")[0].strip()
            group = "en" if lang == "en" else "non-en"
            kind = "memoir" if memoir else "history"
            counts[(kind, group)] += 1
            if group == "non-en":
                langs[lang] += 1
    for k in sorted(counts):
        print(*k, counts[k])
    print("total", sum(counts.values()))
    print("top non-en", langs.most_common(12))


if __name__ == "__main__":
    main(sys.argv[1])
