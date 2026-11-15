"""Count history (LoCC C, D, E, F) + memoir books in pg_catalog.csv, fiction/poetry/drama out.

Rule: wiki-gutenberg-amendments.md "Which books" (operator 2026-11-08), narrowed to history + memoirs.
"""
import csv, sys, collections

IN_WORDS = ("Biography", "Autobiography", "Memoirs", "Diaries", "Correspondence", "Personal narratives")
EXEMPT_LAST = ("History and criticism", "Technique", "Study and teaching", "Bibliography", "Theory")


def subjects(row):
    return [s.strip() for s in row["Subjects"].split(";") if s.strip()]


def is_out(subs, locc):
    if any(c.strip().startswith("PZ") for c in locc.split(";")):
        return True
    for s in subs:
        parts = [p.strip() for p in s.split("--")]
        if parts[-1] in EXEMPT_LAST:
            continue
        low = s.lower()
        if "fiction" in low or "poetry" in low or "drama" in low:
            return True
    return False


def is_memoir(subs, locc):
    if any(c.strip().startswith("CT") for c in locc.split(";")):
        return True
    return any(w.lower() in s.lower() for s in subs for w in IN_WORDS)


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
            memoir = is_memoir(subs, locc)
            if is_out(subs, locc) and not memoir:
                continue
            hist = is_history(locc)
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
