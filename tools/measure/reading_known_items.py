"""MEASUREMENT (not product code): the KNOWN-ITEM query set for the
historical reader (architecture, 2026-11-18: "a KNOWN-ITEM set (TREC), so
nobody needs to judge it"; operator, the same day: "Start with the chat
model let's see what it produces"). docs/notes/historical-languages.md,
"Built (2026-11-18)".

    python -m tools.measure.reading_known_items \\
        --stratum <gutenberg's>:1100:1899 \\
        --kept 60 --weights-sha256 <the chat model's GGUF sha256> --out known_items.jsonl [--seed 1]

Architecture's ruling, 2026-11-18: SEEDED AND STRATIFIED — per stratum
(`--stratum <collection>:<from>:<to>`), records are sampled from its
snapshot and kept only if their OWN year (`reading.years`, the plugin's
declared fields) falls in the stratum's span and their language is Italian;
generation goes on until `--kept` queries of the stratum are kept. From
each kept record one passage (a section's 500-900 characters, cut at a
sentence end) is given to the node's chat model (`llm.chat`, CHAT_URL),
at temperature 0, which writes ONE search query in modern Italian a reader
could type to find that passage's content. The writer sees the ORIGINAL
passage only — never the reader's copy, the record's title or any other hit
(the ruling). The target is that record.

At most PER_WORK queries a WORK (its same-work cluster from `--claims`,
else its title without volume markers, with its author) and PER_AUTHOR an
author, per stratum; each stratum's count of books and works is in the
footer, a short stratum reported as it is, never pooled. Translations (a
`trl` credit or a `translation-of` claim, `reading.years`) are left out — the glossary never reads them —
and counted per stratum (`translations_excluded`). A query is REFUSED, and
kept with its reason for review, if it names the
title, copies four or more consecutive words of the passage, is longer
than `MAX_WORDS` words, or is empty. The FIRST line is the set's header:
the chat model's id (CHAT_MODEL) and weights hash (`--weights-sha256`, as
the operator gives it), the prompt's sha256, the seed, the strata; then one
JSON line per record: qid, stratum, collection, uid, year, passage, query,
refused (null where kept); the LAST line is the count kept and refused,
per stratum.
Read-only on the node apart from `--out`; it calls only the chat model.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

MAX_WORDS = 25
COPY_RUN = 4
PROMPT = (
    "Qui sotto c'è un brano di un libro in italiano antico. Scrivi UNA sola DOMANDA di ricerca, in "
    "italiano moderno, che un lettore di oggi potrebbe scrivere in un motore di ricerca per trovare "
    "proprio questo brano: chiedi di ciò che il brano dice (fatti, idee, luoghi), senza aggiungere nulla "
    "che il brano non dica. Non usare nomi propri di personaggi o di persone, non citare il titolo né "
    "l'autore, non copiare più di tre parole di fila dal brano, al massimo "
    f"{MAX_WORDS} parole, e termina con il punto interrogativo. Rispondi con la sola domanda, senza "
    "virgolette né spiegazioni.\n\nBrano:\n")
#: the sections a passage is never taken from: a book's first ones are its
#: front matter (title page, dedication, notice), München's review 2026-11-18
FRONT_SECTIONS = 2
#: a passage more than this share digits, brackets or capitals is a list,
#: an errata, a title page — not prose a reader would ask about
MAX_NOISE = 0.08

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def noisy(text: str) -> bool:
    """Mostly digits, brackets or capitals: an errata list, a title page."""
    letters = [c for c in text if c.isalpha()]
    odd = sum(1 for c in text if c.isdigit() or c in "[]()|_*=#")
    caps = sum(1 for c in letters if c.isupper())
    return not letters or odd / len(text) > MAX_NOISE or caps / len(letters) > 3 * MAX_NOISE


def passage_of(record: dict, rng: random.Random) -> str | None:
    """A section's 500-900 characters of prose, ending at a sentence end,
    never from the front matter (the first FRONT_SECTIONS of a longer book)."""
    all_secs = list(record.get("sections") or [])
    if len(all_secs) > FRONT_SECTIONS + 1:
        all_secs = all_secs[FRONT_SECTIONS:]
    secs = [str(t) for _h, t in all_secs if len(str(t)) >= 500]
    if not secs:
        return None
    text = rng.choice(secs)
    start = rng.randrange(0, max(1, len(text) - 900))
    m = re.search(r"[.!?;:]\s", text[start:start + 200])
    start = start + m.end() if m and start else start
    chunk = text[start:start + 900]
    end = max(chunk.rfind(". "), chunk.rfind("? "), chunk.rfind("! "))
    got = (chunk[:end + 1] if end >= 500 else chunk).strip()
    return None if noisy(got) else got


def refused(query: str, passage: str, title: str) -> str | None:
    q = query.strip().strip('"«»“”')
    words = [w.lower() for w in _WORD.findall(q)]
    if not words:
        return "empty"
    if len(words) > MAX_WORDS:
        return f"longer than {MAX_WORDS} words"
    if not q.rstrip().endswith("?"):
        return "not a question"
    tw = [w.lower() for w in _WORD.findall(title or "")]
    if tw and len(tw) >= 2 and " ".join(tw[:3]) in " ".join(words):
        return "names the title"
    pw = " " + " ".join(w.lower() for w in _WORD.findall(passage)) + " "
    for i in range(len(words) - COPY_RUN + 1):
        if " " + " ".join(words[i:i + COPY_RUN]) + " " in pw:
            return f"copies {COPY_RUN} words of the passage"
    return None


def records_of(name: str, rng: random.Random):
    from app import collections as collections_mod
    from app.ask.results import _root
    from app.hold import collection as col, shard as shard_mod
    from app.pack import records as pack_records
    from app.share import seeder
    c = collections_mod.by_name(name)
    if c is None:
        raise SystemExit(f"no collection {name}")
    tree = col.shard_dir_of(_root(), name)
    snap = seeder.cached_snapshot(tree)
    if snap is None:
        raise SystemExit(f"{name}: no snapshot held")
    shards = list(snap.shards)
    rng.shuffle(shards)
    for s in shards:
        path = tree / shard_mod.shard_path(s.slice, s.n)
        if not path.is_file():
            continue
        recs = [r for r in pack_records.iter_records(path) if str(r.get("lang") or "").lower().startswith("it")]
        rng.shuffle(recs)
        yield from recs


def _one(out, r: dict, name: str, key: str, y: int, rng: random.Random, made: dict, qid: int, llm) -> int:
    """One passage of the record to the chat model, its query written to the set."""
    passage = passage_of(r, rng)
    if passage is None:
        return qid
    try:
        query = llm.strip_think(llm.chat([{"role": "user", "content": PROMPT + passage}],
                                         temperature=0.0, max_tokens=120)).strip()
    except Exception as e:   # noqa: BLE001 — one failed call is one passage skipped, said
        print(f"{name} {r['uid']}: chat failed: {type(e).__name__}: {e}", file=sys.stderr)
        return qid
    query = query.splitlines()[0].strip().strip('"«»“”') if query else ""
    qid += 1
    why = refused(query, passage, str(r.get("title") or ""))
    made["refused" if why else "kept"] += 1
    out.write(json.dumps({"qid": qid, "stratum": key, "collection": name, "uid": r["uid"], "year": y,
                          "title": r.get("title"), "passage": passage, "query": query, "refused": why},
                         ensure_ascii=False) + "\n")
    out.flush()
    return qid


#: architecture, 2026-11-18: a multi-volume work (Giannone's Istoria civile,
#: 8 of the 1700s' 20) must not fill a stratum
PER_WORK, PER_AUTHOR = 2, 4
_VOLUME = re.compile(r"(?i)\b(?:vol(?:ume)?|tomo|parte|libro|book|canto)\.?\s*(?:[ivxlcdm]+|\d+)\b|[,;:(\[].*$|\d+")


def author_of(r: dict) -> str:
    for a in (r.get("extra") or {}).get("creators_detail") or []:
        if isinstance(a, dict) and str(a.get("role") or "") in ("aut", "author", "cre", "creator"):
            return str(a.get("name") or "").strip().lower()
    return str((r.get("creators") or [""])[0]).strip().lower()


def work_key(r: dict, cluster: dict[str, str]) -> str:
    """The record's WORK: its same-work cluster, else its title without its
    volume and part markers, with its author."""
    if r["uid"] in cluster:
        return "cluster:" + cluster[r["uid"]]
    title = _VOLUME.sub("", str(r.get("title") or "")).strip().lower()
    return f"{title}|{author_of(r)}"


def clusters(claims: Path | None) -> dict[str, str]:
    """{uid: its same-work cluster's root} (union-find over same-work claims)."""
    if claims is None:
        return {}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    with open(claims, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c.get("type") == "same-work":
                a, b = find(str(c.get("subject"))), find(str(c.get("object")))
                if a != b:
                    parent[a] = b
    return {u: find(u) for u in list(parent)}


def stratum(spec: str) -> tuple[str, int, int]:
    name, lo, hi = spec.rsplit(":", 2)
    return name, int(lo), int(hi)


def main(argv: list[str] | None = None) -> int:
    import hashlib
    from app import config, llm, reading
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stratum", action="append", required=True, type=stratum,
                    help="<collection>:<from year>:<to year>")
    ap.add_argument("--kept", type=int, default=60, help="queries kept per stratum")
    ap.add_argument("--claims", type=Path, help="the annotations' claims file: same-work clusters are works")
    ap.add_argument("--per-record", type=int, default=1,
                    help="passages (queries) per record, at most: a stratum of few books still fills")
    ap.add_argument("--weights-sha256", required=True, help="the chat model's weights file sha256")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    qid = 0
    counts: dict[str, dict[str, int]] = {}
    with open(a.out, "w", encoding="utf-8") as out:
        out.write(json.dumps({"set": "reading-known-items", "chat_model": config.CHAT_MODEL,
                              "weights_sha256": a.weights_sha256,
                              "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
                              "temperature": 0, "seed": a.seed,
                              "strata": [f"{n}:{lo}:{hi}" for n, lo, hi in a.stratum]}, ensure_ascii=False) + "\n")
        cluster = clusters(a.claims)
        for name, lo, hi in a.stratum:
            key = f"{name}:{lo}:{hi}"
            made = counts.setdefault(key, {"kept": 0, "refused": 0})
            per_work: dict[str, int] = {}
            per_author: dict[str, int] = {}
            books: set[str] = set()
            for r in records_of(name, rng):
                if made["kept"] >= a.kept:
                    break
                hit = {"chunk_id": f"{r['uid']}:0:1", "source_collection": name}
                y = reading.years([hit]).get(0)
                if y is None or not lo <= y <= hi:
                    continue
                if getattr(y, "translated", False):
                    # the glossary never reads a translation (architecture, 2026-11-18): out of the set
                    made["translations_excluded"] = made.get("translations_excluded", 0) + 1
                    continue
                w, au = work_key(r, cluster), author_of(r)
                for _p in range(a.per_record):
                    if made["kept"] >= a.kept or per_work.get(w, 0) >= PER_WORK \
                            or (au and per_author.get(au, 0) >= PER_AUTHOR):
                        break
                    before = made["kept"]
                    qid = _one(out, r, name, key, y, rng, made, qid, llm)
                    if made["kept"] > before:
                        per_work[w] = per_work.get(w, 0) + 1
                        if au:
                            per_author[au] = per_author.get(au, 0) + 1
                        books.add(r["uid"])
            made["books"] = len(books)
            made["works"] = len(per_work)
            print(f"{key}: {made['kept']} kept, {made['refused']} refused", file=sys.stderr)
        out.write(json.dumps({"counts": counts}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
