"""Italian memoir candidates by title word, not already in the memoir group."""
import csv, re, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import kg_scope_count as k

WORDS = r"memorie|memoria|ricordi|ricordo|rimembranze|diario|taccuino|noterelle|lettere|epistolario|carteggio|autobiografia|vita di|la mia vita|confessioni|mie prigioni|cronaca|cronache"
RX = re.compile(r"\b(" + WORDS + r")\b", re.I)

rows = []
for r in csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")):
    if r["Type"] != "Text" or r["Language"].split(";")[0].strip() != "it":
        continue
    title = " ".join(r["Title"].split())
    m = RX.search(title)
    if not m:
        continue
    subs, locc = k.subjects(r), r["LoCC"]
    if k.is_memoir(subs, locc):
        continue  # already in by the rule
    if k.is_out(subs, locc):
        now = "out (fiction/poetry/drama)"
    elif k.is_history(locc):
        now = "history"
    else:
        now = "other non-fiction (not in scope)"
    author = r["Authors"].split(";")[0].strip()
    rows.append((int(r["Text#"]), title[:90], author[:40], locc, "; ".join(subs)[:110], m.group(1).lower(), now))

order = {"history": 0, "other non-fiction (not in scope)": 1, "out (fiction/poetry/drama)": 2}
rows.sort(key=lambda x: (order[x[6]], x[0]))
print("| # | ebook | title | author | LoCC | subjects | word | now | yes/no |")
print("|---|---|---|---|---|---|---|---|---|")
for i, (n, t, a, l, s, w, now) in enumerate(rows, 1):
    t, a, s = (x.replace("|", "/") for x in (t, a, s))
    print(f"| {i} | [{n}](https://www.gutenberg.org/ebooks/{n}) | {t} | {a} | {l} | {s} | {w} | {now} | |")
print(len(rows), file=sys.stderr)
