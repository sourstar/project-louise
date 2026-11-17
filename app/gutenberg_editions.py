"""THE VERSIONS OF A GUTENBERG WORK, RE-DERIVED BY THE NODE EVERY MONTH
(operator, 2026-11-17: the annotations collection `gutenberg-book-editions`
stops depending on hand-run tools and web APIs; it is re-derived "as if a
monthly Wikipedia snapshot were downloaded", from the node's OWN collections
— "if there is a local wikipedia collection it is pointless to download a
wikipedia snapshot; peers can always check against the snapshot"). The
method `gutenberg-editions` (app/hold/annotation_methods.py), run once a
period of the collection's own chain; docs/notes/annotation-methods.md.

ITS OUTPUT IS THE WHOLE CLAIM SET, TIER BY TIER. A tier that is a pure
function of the node's collections is re-derived; every other claim of the
current snapshot is CARRIED, written again exactly as it stands.

- `wikidata` (`{"kind": "wikidata"}`, always): each book's "Wikipedia page
  about this book" URLs (its record's `extra.description`) → that page in
  the node's wikipedia collection (app/wikipedia_titles.py: the title as
  the MediaWiki API normalises it, a redirect followed) → its Wikidata item
  (page_props) → checked against the node's wikidata collection where it
  is held (an item it lacks was merged or deleted since the page named it).
  A book naming several items claims nothing (an anthology); a work is a
  STAR on its lowest ebook number. The claims are tools/annotations_from_
  works.py's, key for key, so a claim that did not change keeps its uid and
  its bytes. What the node cannot tell — a wiki it does not hold, a title
  not read yet, a run whose dump is not on disk, an item missing from its
  wikidata — is never taken for "no item": a book in that case keeps the
  item the current snapshot's claims give it, through the URL they name,
  and a book they give none claims nothing (said, counted). Nor does a run
  OLDER than a claim (`claim_times`) override it, in either direction — its
  "no item" or its other item: the claim was made against a later Wikipedia
  than the run can speak for.
- `same-title` (app/gutenberg_titles.py) and `language-stage` (the
  historical-markers matcher, app/historical.py): re-derived from the
  gutenberg records alone, and only where the current snapshot holds claims
  of that method (read off the claims' `method`, never assumed).
- carried: every other claim (the classical matcher's, the editions notes',
  a person's): its line of the current claims file, byte for byte. Where a
  re-derived claim is the same claim as a carried one (09 §9.5 identity: the
  same pair, either way round, for `same-work`), the carried one stands.

The order is a rule, so the same inputs give the same bytes: the wikidata
tier in its own order, the carried claims in their lines' order, the
same-title tier, the stage claims by subject — the order the tools' chain
wrote them in (works, merge, titles, language stage). A claim that did not
change is written as its current line.

No network, ever: everything is read from the node's manifests, its SHARD
parts, the local dump and its page_props.
"""
from __future__ import annotations

import ast
import json
import os
import re
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME = "gutenberg-editions"
#: the wikidata tier's attribution, method and confidence, as the claims made
#: by tools/annotations_from_works.py on 2026-10-09 hold them: kept, so that a
#: claim that did not change is re-stated byte for byte, not re-issued.
#: Confidence (30 + 1) / (30 + 2): 30 of 30 hand-checked works were one work
CONTRIBUTOR = "Louise reference node (one-language-per-work measurement, 2026-10-09)"
WIKIDATA = {"kind": "wikidata"}
WIKIDATA_CONFIDENCE = "0.96875"
#: the language-stage matcher's contributor where the snapshot names none
#: (tools/annotations_language_stage.py's default)
STAGE_CONTRIBUTOR = "louise historical-markers"

T_WIKIDATA, T_TITLES, T_STAGE, T_CARRIED = "wikidata", "same-title", "language-stage", "carried"
DERIVED = (T_WIKIDATA, T_TITLES, T_STAGE)
TIERS = DERIVED + (T_CARRIED,)
#: examples of added and withdrawn claims a dry run lists, per tier
EXAMPLES = 20
#: why a title answers no item here besides the resolver's (`Resolved.code`)
NO_WIKIPEDIA, NOT_IN_WIKIDATA = "no one wikipedia", "not in wikidata"
#: a book whose claim is newer than the run that answers otherwise, kept: the
#: run said "no item" (no page, no item, a loop) or named another item
OLDER_NONE = "kept: run older than the claim (no page)"
OLDER_ITEM = "kept: run older than the claim (other item)"
#: the titles a dry run lists behind "no page", at most (each with its books)
NO_PAGE_LISTED = 100
#: books named per listed title, at most
BOOKS_LISTED = 5

#: PG's catalogue line (app/plugins/gutenberg/source.py `WIKIPEDIA_LINE`)
WIKI_LINE = re.compile(r"Wikipedia page about this book:\s*(https?://\S+)")


# --------------------------------------------------------------------------
# a book as the tiers read it

def split_wiki(url: str) -> tuple[str, str] | None:
    """('en.wikipedia.org', 'Title with spaces') or None — as
    tools/measure/one_language_works.py keyed `wiki_map.json`."""
    u = urllib.parse.urlparse(url.strip())
    host = u.netloc.lower()
    if not host.endswith("wikipedia.org"):
        return None
    host = host.replace(".m.wikipedia.org", ".wikipedia.org")
    if u.path.startswith("/wiki/"):
        title = urllib.parse.unquote(u.path[len("/wiki/"):])
    else:
        q = urllib.parse.parse_qs(u.query)
        if "title" not in q:
            return None
        title = q["title"][0]
    title = title.split("#", 1)[0].replace("_", " ").strip()
    return (host, title) if title else None


def host_lang(host: str) -> str:
    """'en.wikipedia.org' → 'en'; '' where the host names no one wiki."""
    head = host[:-len(".wikipedia.org")] if host.endswith(".wikipedia.org") else ""
    return "" if "." in head else head


def _balanced(u: str) -> str:
    """A trailing ")" that closes nothing in the URL dropped (the prose around it)."""
    while u.endswith(")") and u.count(")") > u.count("("):
        u = u[:-1]
    return u


def _lit(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return v
    return v


def book_of(record: dict[str, Any]) -> dict[str, Any]:
    """The fields of a gutenberg record the tiers read — what
    tools/measure/one_language_records.py wrote per record into the
    `records.jsonl` the claims were made from, field for field (the title
    cut at 200 characters and the creators at 6, as those claims hold them;
    `agents` present, possibly empty, wherever the record has a
    `creators_detail` list: the same-title rule falls back to the creators'
    names only where it has none)."""
    ex = record.get("extra") or {}
    desc = ex.get("description") or ""
    out: dict[str, Any] = {"uid": record["uid"], "lang": record.get("lang"),
                           "title": (record.get("title") or "")[:200],
                           "creators": (record.get("creators") or [])[:6]}
    wiki = [_balanced(u.rstrip(".,;")) for u in WIKI_LINE.findall(desc)] if isinstance(desc, str) else []
    if wiki:
        out["wiki"] = wiki
    cd = _lit(ex.get("creators_detail"))
    if isinstance(cd, list):
        out["agents"] = [[c.get("role"), c.get("agent_id"), c.get("name")] for c in cd
                         if isinstance(c, dict) and c.get("agent_id")]
    marc = _lit(ex.get("marc"))
    if isinstance(marc, dict):
        for key, name in (("904", "scan"), ("010", "lccn")):
            if marc.get(key):
                out[name] = str(marc[key][0]).strip()
    return out


def _num(uid: str) -> int:
    return int(uid.split(":", 1)[1])


# --------------------------------------------------------------------------
# the wikidata tier

def found_by_map(book: dict[str, Any], wiki_map: dict) -> dict[str, str]:
    """The items a book names → the first of its URLs naming each, read
    through the Wikipedia API's answers (`wiki_map.json`,
    tools/measure/one_language_works.py) — what tools/annotations_from_works.py
    reads."""
    found: dict[str, str] = {}
    for url in book.get("wiki") or []:
        s = split_wiki(url)
        q = wiki_map.get("titles", {}).get(f"{s[0]}|{s[1]}") if s else None
        if q:
            q = (wiki_map.get("items", {}).get(q) or {}).get("redirect_to") or q
            found.setdefault(q, url)
    return found


def wikidata_claims(books: Iterable[dict[str, Any]],
                    found_of: Callable[[dict[str, Any]], dict[str, str]]) -> list[dict[str, Any]]:
    """The `same-work` claims of the books whose URLs name ONE item
    (`found_of`: the items a book names → the URL naming each first), a STAR
    per item on its lowest ebook number — tools/annotations_from_works.py's
    rule, whatever answered the titles. A pair is claimed once."""
    books = list(books)
    lang = {r["uid"]: (r.get("lang") or "und") for r in books}
    item_books: dict[str, list[tuple[str, str]]] = defaultdict(list)   # item -> [(uid, url)]
    for r in sorted(books, key=lambda r: _num(r["uid"])):
        found = found_of(r)
        if len(found) == 1:                    # several items: an anthology, no claim through it
            (q, url), = found.items()
            item_books[q].append((r["uid"], url))
    out: list[dict[str, Any]] = []
    claimed: set[tuple[str, str]] = set()
    for q in sorted(item_books, key=lambda q: (int(q[1:]) if q[1:].isdigit() else 0, q)):
        books_q = item_books[q]
        if len(books_q) < 2:
            continue
        centre, centre_url = books_q[0]
        for uid, url in books_q[1:]:
            pair = tuple(sorted((uid, centre)))
            if uid == centre or pair in claimed:
                continue
            claimed.add(pair)
            out.append({"type": "same-work", "subject": uid, "object": centre,
                        "subject_lang": lang[uid], "object_lang": lang[centre], "method": WIKIDATA,
                        "evidence": {"wikidata": q, "subject_wikipedia": url, "object_wikipedia": centre_url},
                        "confidence": WIKIDATA_CONFIDENCE, "contributor": CONTRIBUTOR})
    return out


def tier_of(claim: dict[str, Any]) -> str:
    """Which tier made a claim, read off its `method`: a same-title or
    stage claim of any version of its matcher is that tier's."""
    from . import gutenberg_titles, historical
    m = claim.get("method") or {}
    t = claim.get("type")
    if t == "same-work" and m == WIKIDATA:
        return T_WIKIDATA
    if t == "same-work" and m.get("kind") == "matcher" and m.get("id") == gutenberg_titles.METHOD["id"]:
        return T_TITLES
    if t == "language-stage" and m.get("kind") == "matcher" and m.get("id") == historical.MATCHER["id"]:
        return T_STAGE
    return T_CARRIED


# --------------------------------------------------------------------------
# what the current snapshot holds

@dataclass(frozen=True)
class Held:
    """A claim of the current snapshot: as it was submitted (the record's
    claim less what `create` pinned), its line in the claims file and that
    line's bytes where the file holds them (and they state no pin)."""
    claim: dict[str, Any]
    line: int
    raw: bytes | None
    tier: str
    uid: str = ""             # its record's uid: a claim keeps it from snapshot to snapshot


def held_claims(root: Path, name: str) -> tuple[str | None, list[Held]]:
    """(the current snapshot's id, its claims in their lines' order)."""
    from app.share import seeder

    from .hold import annotations as ann
    from .hold import collection as col
    from .pack import records as pack_records
    tree = col.shard_dir_of(root, name)
    snap = seeder.current_snapshot(tree)
    if snap is None:
        return None, []
    lines: dict[str, bytes] = {}
    claims_file = Path(root) / name / "origin" / ann.CLAIMS_FILE
    if claims_file.is_file():
        for raw in claims_file.read_bytes().split(b"\n"):
            raw = raw[:-1] if raw.endswith(b"\r") else raw
            if raw.strip():
                lines.setdefault(ann.blake128(raw), raw)
    out: list[Held] = []
    for s in snap.shards:
        part = ann._part_file(tree, s, None, snap.snapshot_id)
        if part is None:
            raise ann.AnnotationsError(f"{name}: this peer holds no file with the root snapshot "
                                       f"{snap.snapshot_id} lists for {s.path}")
        for r in pack_records.iter_records(part):
            c = {k: v for k, v in ann.claim_of(r).items() if k not in ann.PINNED_KEYS}
            raw = lines.get(r.get("origin_hash"))
            if raw is not None and any(k in json.loads(raw) for k in ann.PINNED_KEYS):
                raw = None                     # a stated pin would have to agree with the new text
            origin = str(r.get("origin") or "")
            line = int(origin.rsplit("#", 1)[1]) if origin.rsplit("#", 1)[-1].isdigit() else 0
            out.append(Held(c, line, raw, tier_of(c), str(r.get("uid") or "")))
    out.sort(key=lambda h: (h.line, json.dumps(h.claim, sort_keys=True)))
    return snap.snapshot_id, out


def claim_times(root: Path, name: str, uids: Iterable[str]) -> dict[str, str | None]:
    """Each claim record's uid → when the claim was made AT THE LATEST: the
    earliest signing time of the first snapshot of `name` that holds it, or
    of the first signed snapshot after that one; None where none from its
    first on is signed (`_signed_at`).

    NOTHING RECORDS A CLAIM'S OWN MAKING DATE (2026-11-17): 09 §9.5's claim
    has no date key and its record's `date` is null; a contributor's text
    ("…, 2026-10-09") is not a field; a snapshot's id is its period, not its
    writing (`create` names the period that closed, an update may catch up on
    one long past). So the snapshot in which the claim first appears is its
    date, and that snapshot's signature says when it was written. A claim
    keeps its number, its uid, from snapshot to snapshot, and a new one takes
    the next (`annotations.update`): its first snapshot is the first whose
    delta adds the uid. A uid no held snapshot adds is older than all of
    them, and the oldest held bounds it."""
    from .hold import collection as col
    from .hold import shard as shard_mod
    from .hold import snapshot as sn
    from .hold.annotations import _SNAP_RE
    tree = col.shard_dir_of(root, name)
    try:
        files = sorted(p for p in (tree / "snapshots").glob("*.json")
                       if not p.name.endswith(".sig.json") and _SNAP_RE.match(p.name[:-len(".json")]))
    except OSError:
        files = []
    first: dict[str, int] = {}
    signed: list[str | None] = []
    for i, f in enumerate(files):
        try:
            snap = sn.load(f)
            for u in snap.delta.added:
                first.setdefault(u, i)
            signed.append(_signed_at(tree / shard_mod.snapshot_sig_path(snap.snapshot_id), snap.hash))
        except Exception:                      # noqa: BLE001 — a snapshot this peer cannot read dates nothing
            signed.append(None)
    bound: list[str | None] = [None] * (len(files) + 1)
    for i in range(len(files) - 1, -1, -1):
        bound[i] = signed[i] if signed[i] is not None else bound[i + 1]
    return {u: bound[first.get(u, 0)] for u in uids}


def _signed_at(sidecar: Path, snapshot_hash: str) -> str | None:
    """The earliest time a snapshot's sidecar records a signature at (09
    §9.4; a sidecar naming another snapshot says nothing about this one)."""
    try:
        doc = json.loads(sidecar.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("snapshot") != snapshot_hash:
        return None
    times = [g["time"] for g in doc.get("signatures") or [] if isinstance(g, dict) and isinstance(g.get("time"), str)]
    return min(times) if times else None


def made_after(times: list[str | None], run: str | None) -> bool:
    """Whether a claim may have been made after `run` was cut — any of
    `times` (`claim_times`); an unknown time or run may have been — so that
    what that run answers otherwise ("no item", another item) is no evidence
    against it. A run is dated by its day (`YYYYMMDD`) and a claim by its
    bound's: a claim of the run's own day is taken as made after it."""
    from .hold.origin_run import RUN_RE
    if run is None or not RUN_RE.match(run) or not times or any(t is None for t in times):
        return True
    return f"{run[:4]}-{run[4:6]}-{run[6:]}" <= max(str(t)[:10] for t in times)


# --------------------------------------------------------------------------
# the node's collections

def _held_of(plugin: str) -> list:
    """The collections this node runs of `plugin` (their manifests)."""
    from . import collections as collections_mod
    served, _ = collections_mod.load_all(skip_unreadable=True)
    return [c for c in served if c.plugin == plugin]


def read_part(path: str, contributor: str | None, memo: str | None = None,
              keep: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One part of the target's snapshot (a pool task): every record as
    `book_of` reads it, and the stage matcher's claims when `contributor`
    is given (tools/annotations_language_stage.py `scan_part`). With `memo`,
    the part's file in the part cache (`BOOKS`): read from it where it is,
    and — `keep` — written where it is not."""
    got = _cached(memo) if memo else None
    if got is None:
        got = _read_part(path)
        if memo and keep:
            _store(memo, got)
    books, stages = got
    return books, ([dict(c, contributor=contributor) for c in stages] if contributor is not None else [])


#: THE PART CACHE (2026-11-17, docs/notes/collection-updates.md §4: the
#: cascade "from the delta, never a full re-scan"): what `read_part` gives of
#: a part, kept in one file named by the part's ROOT (app/ask/part_cache.py) —
#: the same bytes give the same books, so a part gutenberg did not re-issue is
#: never read again — and by `books_version`, the digest of the code that
#: reads them: any change to this module or to the stage matcher is another
#: name, so a file is never stale (invariant 3). On the fast drive when one is
#: named (invariant 6). The stage claims are kept with the contributor
#: `_ANY` and given the run's own when read: the matcher's only use of it.
BOOKS_KIND = "gutenberg-editions-books"
_ANY = ""


def books_version() -> int:
    """The digest of this module's and the stage matcher's source, as the
    part cache's version."""
    import hashlib

    from . import historical
    h = hashlib.blake2b(digest_size=7)
    for p in (Path(__file__), Path(historical.__file__)):
        h.update(p.read_bytes())
    return int.from_bytes(h.digest(), "big")


def cache_store(tree: Path) -> Path:
    """The part cache's directory for gutenberg's SHARD tree `tree`."""
    from .ask.part_cache import PartCache
    return PartCache(BOOKS_KIND, books_version()).store(tree)


def _read_part(path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from . import historical
    from .pack import records as pack_records
    books, stages = [], []
    for r in pack_records.iter_records(Path(path)):
        books.append(book_of(r))
        c = historical.stage_claim(r, _ANY)
        if c is not None:
            stages.append(c)
    return books, stages


def _cached(memo: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    try:
        got = json.loads(Path(memo).read_bytes())
        return list(got["books"]), list(got["stages"])
    except (OSError, ValueError, KeyError, TypeError):
        return None                         # absent or unreadable: the part is read


def _store(memo: str, got: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
    """Written whole or not at all (a sibling renamed): derived, so a file
    that cannot be written is read again next time."""
    p = Path(memo)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_bytes(json.dumps({"books": got[0], "stages": got[1]}, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, p)
    except OSError:
        pass


def items_held(manifest: Path, items: Iterable[str]) -> set[str]:
    """The items of `items` the wikidata collection's manifest holds — by
    its unique index (source, native_id), a few hundred at a query."""
    from . import wikipedia_titles
    from .plugins.wikidata import ids
    by_native = {ids.native_id_of(q): q for q in items if ids.is_entity_id(q)}
    got: set[str] = set()
    keys = sorted(by_native)
    con = wikipedia_titles._ro(manifest)
    try:
        for i in range(0, len(keys), wikipedia_titles.LOOKUP):
            batch = keys[i:i + wikipedia_titles.LOOKUP]
            for (nid,) in con.execute(f"SELECT native_id FROM documents WHERE source = ? AND native_id IN "
                                      f"({','.join('?' * len(batch))})", ["wikidata", *batch]):
                got.add(by_native[str(nid)])
    finally:
        con.close()
    return got


def _newest(root: Path, name: str) -> str | None:
    from .hold import annotation_methods as am
    from .hold import collection as col
    return am.newest_snapshot_id(col.shard_dir_of(root, name))


def resolutions(root: Path, books: list[dict[str, Any]], *, log=print):
    """Every URL key (`split_wiki`) of the books → what the node's own
    wikipedia answers for it (`wikipedia_titles.Resolved`), the item checked
    against the node's wikidata; and the inputs read, by collection name."""
    from . import wikipedia_titles as wt
    keys: dict[tuple[str, str], tuple[str, str]] = {}
    for b in books:
        for url in b.get("wiki") or []:
            s = split_wiki(url)
            if s:
                keys[s] = (host_lang(s[0]), s[1])
    inputs: dict[str, str | None] = {}
    wikis = _held_of("wikipedia")
    if len(wikis) == 1:
        w = wikis[0]
        inputs[w.name] = _newest(root, w.name)
        # the run the collection holds, and its dumps: what proves "no page"
        run = getattr(w, "snapshot", None)
        dumps = wt.run_dumps(getattr(w, "corpus_dir", None), run)
        got = wt.resolve(w.manifest_path, set(keys.values()), dumps=dumps, run=run, log=log,
                         name=w.name) if keys else {}
        res = {s: got[keys[s]] for s in keys}
    else:
        why = ("this node holds no wikipedia collection" if not wikis else
               f"this node holds {len(wikis)} wikipedia collections ({', '.join(sorted(c.name for c in wikis))}), "
               f"and which answers is not guessed")
        res = {s: wt.Resolved(wt.UNKNOWN, None, NO_WIKIPEDIA, why) for s in keys}
    wds = _held_of("wikidata")
    if len(wds) == 1 and res:
        inputs[wds[0].name] = _newest(root, wds[0].name)
        present = items_held(wds[0].manifest_path, {r.item for r in res.values() if r.kind == wt.ITEM})
        res = {s: (r if r.kind != wt.ITEM or r.item in present else
                   wt.Resolved(wt.UNKNOWN, None, NOT_IN_WIKIDATA,
                               f"{r.item} is not an item the node's wikidata holds: merged or deleted since "
                               f"{r.page} named it", r.page, r.redirects, r.run))
               for s, r in res.items()}
    return res, inputs


# --------------------------------------------------------------------------
# the derivation

@dataclass
class Derivation:
    """The method's whole output and what it was made from."""
    lines: list[tuple[str, dict[str, Any], bytes]]     # (tier, claim, its line)
    held: list[Held]
    active: dict[str, bool]
    inputs: dict[str, str | None]
    target_uids: frozenset[str]
    resolution: dict[str, Any]
    shadowed: dict[str, int] = field(default_factory=dict)   # made again by a carried claim or an earlier tier
    #: per book of the wikidata tier: each URL's answer and the items it names
    #: (`found_of`), read back by the report for a pair that comes apart
    books: dict[str, dict[str, Any]] = field(default_factory=dict)

    def bytes(self) -> bytes:
        return b"".join(line + b"\n" for _, _, line in self.lines)


def _dumps(c: dict[str, Any]) -> bytes:
    """A line as tools/annotations_from_works.py and _from_titles wrote it."""
    return json.dumps(c, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _canonical(c: dict[str, Any]) -> bytes:
    """A line as tools/annotations_language_stage.py wrote it."""
    from .hold import codex as cx
    return cx.canonical_json(c)


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from .hold import annotations as ann
    from .hold import codex as cx
    return cx.canonical_json(ann._nfc(a)) == cx.canonical_json(ann._nfc(b))


def derive(root: Path, name: str, target: str, *, log=print, keep: bool = True) -> Derivation:
    """The whole claim set, tier by tier, from the current snapshot of `name`
    and the target `target`'s, the node's wikipedia and wikidata. The
    target's parts are read through the part cache (`BOOKS_KIND`): only a
    part whose root has no file yet is read — and, `keep`, its file written
    and the files of parts the snapshot no longer lists removed."""
    from app.share import seeder

    from . import gutenberg_titles, parallel
    from . import wikipedia_titles as wt
    from .hold import annotations as ann
    from .hold import collection as col
    root = Path(root)
    _, held = held_claims(root, name)
    active = {T_WIKIDATA: True, T_TITLES: any(h.tier == T_TITLES for h in held),
              T_STAGE: any(h.tier == T_STAGE for h in held)}
    contributor = None
    if active[T_STAGE]:
        named = Counter(h.claim.get("contributor") for h in held if h.tier == T_STAGE)
        contributor = min(named.items(), key=lambda kv: (-kv[1], str(kv[0])))[0] or STAGE_CONTRIBUTOR

    # the target's records: ONE pass over its snapshot's parts, on the parse pool
    tree = col.shard_dir_of(root, target)
    snap = seeder.current_snapshot(tree)
    if snap is None or not snap.shards:
        raise ann.AnnotationsError(f"the target {target} holds no snapshot to derive the claims from")
    from .ask.part_cache import PartCache
    cache = PartCache(BOOKS_KIND, books_version())
    store = cache.store(tree)
    paths, memos = [], []
    for s in snap.shards:
        p = ann._part_file(tree, s, None, snap.snapshot_id)
        if p is None:
            raise ann.AnnotationsError(f"{target}: this peer holds no file with the root snapshot "
                                       f"{snap.snapshot_id} lists for {s.path}")
        paths.append(str(p))
        memos.append(str(cache.file(store, s)))
    held_memos = sum(1 for m in memos if Path(m).is_file())
    books: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    for b, st in parallel.each_part(read_part, [(p, contributor, m, keep) for p, m in zip(paths, memos)]):
        books.extend(b)
        stages.extend(st)
    log(f"[annotations] {NAME}: {len(books):,} records of {target} {snap.snapshot_id} ({len(paths)} part(s): "
        f"{len(paths) - held_memos} read, {held_memos} unchanged, from the part cache)")
    if keep:
        _prune(store, set(memos))

    # the wikidata tier
    res, inputs = resolutions(root, books, log=log)
    inputs = {target: snap.snapshot_id, **inputs}
    carried_item: dict[str, tuple[str, str]] = {}
    wd_held = [h for h in held if h.tier == T_WIKIDATA]
    times = claim_times(root, name, [h.uid for h in wd_held])
    claimed_at: dict[str, list[str | None]] = defaultdict(list)     # book -> when its claims were made, at the latest
    for h in wd_held:
        ev = h.claim.get("evidence") or {}
        carried_item.setdefault(h.claim["subject"], (ev.get("wikidata"), ev.get("subject_wikipedia")))
        carried_item.setdefault(h.claim["object"], (ev.get("wikidata"), ev.get("object_wikipedia")))
        for end in (h.claim["subject"], h.claim["object"]):
            claimed_at[end].append(times.get(h.uid))
    books_by: Counter = Counter()
    traces: dict[str, dict[str, Any]] = {}

    def found_of(book: dict[str, Any]) -> dict[str, str]:
        """The items a book names, by the node's answers: a URL the node
        cannot answer takes the item the current claims give the book
        through that very URL, and so does one a run OLDER than the book's
        claims (`made_after`) answers otherwise — "no item" or another item:
        an older run never overrides a newer claim, in either direction. Any
        other is left out, and a book with a URL the node cannot answer and
        no item of its own in the current claims claims nothing."""
        found: dict[str, str] = {}
        mine = carried_item.get(book["uid"])
        undetermined = kept = False
        older: set[str] = set()
        answers: list[dict[str, Any]] = []
        for url in book.get("wiki") or []:
            s = split_wiki(url)
            r = res.get(s) if s else None
            if r is None:
                continue
            answers.append({"url": url, "answer": r.kind, "item": r.item, "code": r.code})
            claimed = mine is not None and url == mine[1] and bool(mine[0])
            if r.kind == wt.UNKNOWN:
                if claimed:
                    found.setdefault(mine[0], url)
                    kept = True
                elif mine is None:
                    undetermined = True
            elif claimed and r.item != mine[0] and made_after(claimed_at[book["uid"]], r.run):
                found.setdefault(mine[0], url)       # the claim is newer than the run: the run is no evidence
                older.add(OLDER_NONE if r.kind == wt.NONE else OLDER_ITEM)
            elif r.kind == wt.ITEM:
                found.setdefault(r.item, url)
        if book.get("wiki"):
            books_by["with a Wikipedia line"] += 1
        if kept:
            books_by["their item kept through a URL the node cannot answer"] += 1
        for why in older:
            books_by[why] += 1
        traces[book["uid"]] = {"urls": answers, "named": sorted(found), "undetermined": undetermined}
        if undetermined:
            books_by["undetermined: a URL the node cannot answer, and no item in the current claims"] += 1
            return {}
        return found
    t1 = wikidata_claims(books, found_of)
    kinds = Counter(r.kind for r in res.values())
    no_page: dict[tuple[str, str], set[str]] = defaultdict(set)
    for b in books:
        for url in b.get("wiki") or []:
            s = split_wiki(url)
            if s and s in res and res[s].code == wt.NO_PAGE:
                no_page[s].add(b["uid"])
    resolution = {"titles": len(res), "items": kinds.get(wt.ITEM, 0), "none": kinds.get(wt.NONE, 0),
                  "unknown": kinds.get(wt.UNKNOWN, 0),
                  "redirects_followed": sum(1 for r in res.values() if r.kind == wt.ITEM and r.redirects),
                  "found_by_page_id": sum(1 for r in res.values() if r.code == wt.BY_PAGE_ID),
                  "runs": sorted({r.run for r in res.values() if r.run}),
                  "by_reason": dict(sorted(Counter(f"{r.kind}: {r.code}" for r in res.values()
                                                   if r.kind != wt.ITEM).items())),
                  "wikis_not_held": dict(sorted(Counter(host_lang(s[0]) or s[0] for s, r in res.items()
                                                        if r.code == wt.NOT_HELD).items())),
                  "books": dict(sorted(books_by.items())),
                  # title by title: every "no page" (at most NO_PAGE_LISTED), and the titles the manifest
                  # holds under another (at most EXAMPLES) — what the next dry run is read by
                  "no_page_titles": [{"wiki": host_lang(s[0]) or s[0], "title": s[1],
                                      "books": sorted(u, key=_num)[:BOOKS_LISTED]}
                                     for s, u in sorted(no_page.items())[:NO_PAGE_LISTED]],
                  "by_page_id_titles": [{"wiki": host_lang(s[0]) or s[0], "title": s[1], "page": r.page,
                                         "manifest_title": r.stored}
                                        for s, r in sorted(res.items()) if r.code == wt.BY_PAGE_ID][:EXAMPLES]}

    # the same-title and stage tiers, where the snapshot holds them
    carried = [h for h in held if h.tier == T_CARRIED]
    t2: list[dict[str, Any]] = []
    if active[T_TITLES]:
        existing = [c for c in (*t1, *(h.claim for h in carried)) if c.get("type") in ann.WORK_TYPES]
        t2 = gutenberg_titles.claims_of(books, existing)
    t3 = sorted(stages, key=lambda c: c["subject"]) if active[T_STAGE] else []

    # assembled: a carried claim stands over a re-derived one; an unchanged
    # claim is written as its current line
    held_by = {ann.identity_key(h.claim): h for h in held}
    seen = {ann.identity_key(h.claim) for h in carried}
    lines: list[tuple[str, dict[str, Any], bytes]] = []
    shadowed: Counter = Counter()

    def emit(tier: str, cs: list[dict[str, Any]], render) -> None:
        for c in cs:
            k = ann.identity_key(c)
            if k in seen:
                shadowed[tier] += 1
                continue
            seen.add(k)
            h = held_by.get(k)
            lines.append((tier, c, h.raw if h is not None and h.raw is not None and _same(h.claim, c)
                          else render(c)))
    emit(T_WIKIDATA, t1, _dumps)
    lines.extend((T_CARRIED, h.claim, h.raw if h.raw is not None else _dumps(h.claim)) for h in carried)
    emit(T_TITLES, t2, _dumps)
    emit(T_STAGE, t3, _canonical)
    return Derivation(lines, held, active, inputs, frozenset(b["uid"] for b in books), resolution,
                      dict(shadowed), traces)


def _prune(store: Path, current: set[str]) -> None:
    """The part cache's files of parts (or code) the current snapshot no
    longer lists: removed, so the cache holds one file a part."""
    try:
        old = [p for p in Path(store).glob("v*.json") if str(p) not in current]
    except OSError:
        return
    for p in old:
        try:
            p.unlink()
        except OSError:
            pass


def _end_note(info: dict[str, Any] | None) -> dict[str, Any]:
    """One end of a pair that came apart, as a dry run shows it: its URLs,
    the node's answer to each, and why it is or is not in a star."""
    if info is None:
        return {"why": "not among the books the wikidata tier read"}
    named = info.get("named") or []
    if info.get("undetermined"):
        why = "a URL the node cannot answer and no item in the current claims: it claims nothing"
    elif not named:
        why = "names no item"
    elif len(named) > 1:
        why = f"names several items ({', '.join(named)}): an anthology, no claim through it"
    else:
        why = f"names {named[0]}"
    return {"why": why, "urls": info.get("urls") or []}


def _brief(c: dict[str, Any]) -> dict[str, Any]:
    """A claim as a dry run lists it."""
    return {k: c[k] for k in ("type", "subject", "object", "method", "evidence") if k in c}


def report(d: Derivation) -> dict[str, Any]:
    """Per tier: the claims it makes now (`re_derived`; `carried` for the
    carried), those kept identical, changed, added, withdrawn, and set aside
    (an end the target's snapshot does not hold: `update` names and skips
    them), with up to `EXAMPLES` added and withdrawn claims each. A
    withdrawn work claim says whether its two books are still one work
    through the claims made now (`withdrawn_still_one_work`, and `now` on
    each example) or apart."""
    from .hold import annotations as ann
    held_by = {ann.identity_key(h.claim): h for h in d.held}
    made = {ann.identity_key(c) for _, c, _ in d.lines}
    tiers: dict[str, dict[str, Any]] = {}
    for t in TIERS:
        row: dict[str, Any] = {"re_derived": 0, "kept_identical": 0, "changed": 0, "added": 0,
                               "withdrawn": 0, "set_aside": 0}
        if t == T_CARRIED:
            row = {"carried": 0, "set_aside": 0}
        elif not d.active[t]:
            row = {"re_derived": False, "why": "the current snapshot holds no claim of this method, so the "
                                              "tier is not made (its claims, if any, are carried)"}
        else:
            row["examples"] = {"added": [], "withdrawn": []}
            if d.shadowed.get(t):
                row["already_claimed"] = d.shadowed[t]
        tiers[t] = row
    for tier, c, _ in d.lines:
        row = tiers[tier]
        if any(u not in d.target_uids for u in ann.record_ends(c)):
            row["set_aside"] += 1
        if tier == T_CARRIED:
            row["carried"] += 1
            continue
        row["re_derived"] += 1
        h = held_by.get(ann.identity_key(c))
        if h is None:
            row["added"] += 1
            if len(row["examples"]["added"]) < EXAMPLES:
                row["examples"]["added"].append(_brief(c))
        elif _same(h.claim, c):
            row["kept_identical"] += 1
        else:
            row["changed"] += 1
    # WHAT BECAME OF A WITHDRAWN CLAIM (München's dry run, 2026-11-17): its two
    # books may still be one work through the claims made now (another tier,
    # another star's centre) or be apart. The made claims' work links, as a
    # union-find over their ends, answer it per withdrawn claim.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for _, c, _ in d.lines:
        if c.get("type") in ann.WORK_TYPES and c.get("subject") and c.get("object"):
            parent[find(c["subject"])] = find(c["object"])
    for k, h in held_by.items():
        if k in made:
            continue
        row = tiers[h.tier]
        if "withdrawn" in row:
            row["withdrawn"] += 1
            c = h.claim
            joined = (c.get("type") in ann.WORK_TYPES and bool(c.get("subject")) and bool(c.get("object"))
                      and find(c["subject"]) == find(c["object"]))
            if joined:
                row["withdrawn_still_one_work"] = row.get("withdrawn_still_one_work", 0) + 1
            if len(row["examples"]["withdrawn"]) < EXAMPLES:
                ex = {**_brief(c), "now": ("still one work through the claims made now" if joined
                                           else "apart: no claim made now joins the two")}
                if not joined and c.get("subject") and c.get("object"):
                    ex["ends"] = {u: _end_note(d.books.get(u)) for u in (c["subject"], c["object"])}
                row["examples"]["withdrawn"].append(ex)
    return {"method": NAME, "claims": len(d.lines), "held": len(d.held), "against": d.inputs,
            "tiers": tiers, "resolution": d.resolution}


def produce(root: Path, name: str, target: str, out: Path, *, log=print) -> dict[str, Any]:
    """The method's whole claim set written to `out` (a temporary renamed
    over it); the counts, with the inputs read under `against`."""
    d = derive(root, name, target, log=log)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(d.bytes())
    tmp.replace(out)
    by = Counter(t for t, _, _ in d.lines)
    log(f"[annotations] {NAME} over {target}: {len(d.lines):,} claims — "
        + ", ".join(f"{by.get(t, 0):,} {t}" for t in TIERS)
        + f"; titles answered by the node: {d.resolution['items']:,} items, {d.resolution['none']:,} none, "
          f"{d.resolution['unknown']:,} not answerable here")
    return {"claims": len(d.lines), "tiers": {t: by.get(t, 0) for t in TIERS}, "against": d.inputs,
            "resolution": {k: v for k, v in d.resolution.items() if not isinstance(v, (dict, list))}}


def preview(root: Path, name: str, target: str, *, log=print) -> dict[str, Any]:
    """The dry run: what a derivation would change, per tier. Writes nothing
    (the part cache is read, not written)."""
    return report(derive(root, name, target, log=log, keep=False))
