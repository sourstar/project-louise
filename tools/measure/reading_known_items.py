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

A query is REFUSED, and kept with its reason for review, if it names the
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
    "Qui sotto c'è un brano di un libro o di un atto in italiano antico. Scrivi UNA sola domanda di "
    "ricerca, in italiano moderno, che un lettore di oggi potrebbe scrivere in un motore di ricerca per "
    "trovare proprio questo testo, basandoti solo su ciò che il brano dice (persone, fatti, luoghi, idee). "
    "Non citare il titolo né l'autore, non copiare più di tre parole di fila dal brano, al massimo "
    f"{MAX_WORDS} parole. Rispondi con la sola domanda, senza virgolette né spiegazioni.\n\nBrano:\n")

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def passage_of(record: dict, rng: random.Random) -> str | None:
    """A section's 500-900 characters, ending at a sentence end."""
    secs = [str(t) for _h, t in (record.get("sections") or []) if len(str(t)) >= 500]
    if not secs:
        return None
    text = rng.choice(secs)
    start = rng.randrange(0, max(1, len(text) - 900))
    m = re.search(r"[.!?;:]\s", text[start:start + 200])
    start = start + m.end() if m and start else start
    chunk = text[start:start + 900]
    end = max(chunk.rfind(". "), chunk.rfind("? "), chunk.rfind("! "))
    return (chunk[:end + 1] if end >= 500 else chunk).strip()


def refused(query: str, passage: str, title: str) -> str | None:
    q = query.strip().strip('"«»“”')
    words = [w.lower() for w in _WORD.findall(q)]
    if not words:
        return "empty"
    if len(words) > MAX_WORDS:
        return f"longer than {MAX_WORDS} words"
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
        for name, lo, hi in a.stratum:
            key = f"{name}:{lo}:{hi}"
            made = counts.setdefault(key, {"kept": 0, "refused": 0})
            for r in records_of(name, rng):
                if made["kept"] >= a.kept:
                    break
                hit = {"chunk_id": f"{r['uid']}:0:1", "source_collection": name}
                y = reading.years([hit]).get(0)
                if y is None or not lo <= y <= hi:
                    continue
                for _p in range(a.per_record):
                    if made["kept"] >= a.kept:
                        break
                    qid = _one(out, r, name, key, y, rng, made, qid, llm)
            print(f"{key}: {made['kept']} kept, {made['refused']} refused", file=sys.stderr)
        out.write(json.dumps({"counts": counts}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
