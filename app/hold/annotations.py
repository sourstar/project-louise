"""Annotations collections — claims about another collection's records
(docs/02-collections.md §2.10; docs/09-formats.md §9.3, §9.5, §9.14; the
design: docs/notes/annotation-collections.md). Operator, 2026-10-09.

A plugin's relations are what its ORIGIN states (05 §5.5): "this law amends
that one". A link WE find — "this Latin book and that English one are the
same work", made by a matcher, by Wikidata or by a person — is not stated
by Project Gutenberg, so it cannot be a relation of the gutenberg
collection. It is a side contribution: a collection of its own whose
records are CLAIMS about the target's records, whose relations member is
those claims as typed edges (here the annotator IS the source, and the
edges are its explicit statements), and which is shared, checked and
adopted like any collection. Nothing in the target changes.

    claim  = {type, subject, object, subject_lang, object_lang,
              method, evidence, confidence, contributor,
              subject_hash, object_hash, target_snapshot}

`type` is `same-work` (two records are versions of one work in different
languages — neither is said to be the other's source) or `translation-of`
(the subject translates the object: source language = `object_lang`,
target language = `subject_lang`). `subject_hash` / `object_hash` are the
two target records' `content_hash` as the target snapshot the claim was
checked against (`target_snapshot`) holds them: `create` reads them from
that snapshot's parts and refuses a claim about a record it does not hold.

THE TIME RULE (02 §2.10). The Codex names the target snapshot the claims
hold from (`annotates.from_snapshot`); no claim is attached to an answer
read from an earlier target snapshot. Read from a later one, a claim whose
two hashes still equal the target's is `current`; one whose record has
changed since is `stale` — still shown, labelled, because the claim was
made about other text. A deterministic method re-checks by re-running and
publishing the next annotations snapshot with the new hashes; nothing on a
reading peer re-runs a method.

A claim record is a 09 §9.5 record line: `sections` is one section,
`["claim", <the claim's canonical JSON>]`, so that `content_hash` covers
the whole claim and the `records` check verifies it unchanged; `origin` is
the claims file line it came from and `origin_hash` that line's bytes.

THE GRAPH AMENDMENT (operator, 2026-10-20; 02 §2.10, 09 §9.3, §9.5, §9.14;
docs/notes/wiki-gutenberg-amendments.md A-K1–A-K4). Three more claim
types, whose ends are CHUNKS and ENTITIES rather than two records:

    mention    subject = a chunk of the text target {uid, chunk_id, text_hash},
               object  = an entity's canonical id (`wikidata:Q42`),
               own keys start, end (code points in the chunk, end exclusive),
               surface (the text between them)
    relation   subject, object = entities; relation = a property's canonical
               id (`wikidata:P50`); evidence_chunk = the chunk it was read from
    community  subject = {id, level}; object null; members = sorted entity ids,
               at least two; summary

An annotations Codex may annotate SEVERAL collections (Codex version 9,
`annotates` a list): wiki-gutenberg annotates gutenberg, whose chunks its
mentions are read from, and wikidata, whose records its entities are. A
claim's end belongs to the target whose plugin its uid prefix names; an
entity's canonical id becomes a record uid through the plugin's id rule
(`entity_uid`). `create` pins the RECORD at each end (`subject_hash`,
`object_hash` — both null for a community, which has no record at either
end) from the target snapshot, and CHECKS a chunk end by re-cutting its
record with the target's chunker (invariant 3: the same text cuts the same
chunks) — the chunk must exist, hash to `text_hash`, and hold the mention's
`surface` at `start:end`. A `model` method (a model whose output is not
bit-exact) names its weights and its prompt by digest and runs at
temperature 0; its claims are verified by the `model-agreement` check (03
§3.2), not by hash. The Codex stays at version 7 for a one-target
collection of the two 2026-10-09 types in JSONL parts, so nothing already
written moves.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import secrets
import shutil
import sys
import time
import unicodedata
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from . import codex as cx
from .codex import Codex, CodexError

PLUGIN = cx.ANNOTATIONS_PLUGIN
SAME_WORK, TRANSLATION_OF = "same-work", "translation-of"
MENTION, RELATION, COMMUNITY = "mention", "relation", "community"
#: the types of 2026-10-09: two records of ONE target
WORK_TYPES = (SAME_WORK, TRANSLATION_OF)
#: the types of 2026-10-20 (Codex GRAPH_VERSION): chunk and entity ends
GRAPH_TYPES = (MENTION, RELATION, COMMUNITY)
CLAIM_TYPES = WORK_TYPES + GRAPH_TYPES
#: a community's edges: each member → the community claim (09 §9.14)
MEMBER_OF = "member-of"
METHOD_KINDS = ("matcher", "wikidata", "human", "model")
#: a method whose output is not bit-exact: what a verifier re-runs (02 §2.10)
MODEL_METHOD_KEYS = ("kind", "id", "version", "model", "prompt", "temperature")
#: what an annotator writes in the claims file: the keys every claim has …
COMMON_KEYS = ("type", "subject", "object", "method", "evidence", "confidence", "contributor")
#: … and each type's own (09 §9.5)
OWN_KEYS: dict[str, tuple[str, ...]] = {
    SAME_WORK: ("subject_lang", "object_lang"),
    TRANSLATION_OF: ("subject_lang", "object_lang"),
    MENTION: ("start", "end", "surface"),
    RELATION: ("relation", "evidence_chunk"),
    COMMUNITY: ("members", "summary"),
}
#: the 2026-10-09 claim's keys, as callers knew them
CLAIM_KEYS = COMMON_KEYS[:3] + OWN_KEYS[SAME_WORK] + COMMON_KEYS[3:]
#: what `create` pins from the target snapshot (an annotator MAY state them;
#: then they must agree)
PINNED_KEYS = ("subject_hash", "object_hash", "target_snapshot")
#: a chunk end (A-K2): the record, the chunk's id and the BLAKE2b-128 of
#: its text as the target's chunker cut it (06 §6.3, `embeddings.chunk_text_hash`)
CHUNK_END_KEYS = ("uid", "chunk_id", "text_hash")
#: the kinds of end a claim has (`ends_of`)
RECORD, CHUNK, ENTITY = "record", "chunk", "entity"
SLICE = "all"
SHARD_RECORDS = 50000
#: 09 §9.3: records per part for a claims collection in Parquet
SHARD_RECORDS_PARQUET = 1_000_000
PARSER = cx.Parser(PLUGIN, 1)
CLAIMS_FILE = "claims.jsonl"
FORM = ("the claims file as its annotator submitted it, one JSON claim per line: the "
        "annotator is this collection's origin, so no public origin re-produces it (02 §2.10)")
CURRENT, STALE, UNKNOWN = "current", "stale", "unknown"

_LANG_RE = re.compile(r"^([a-z]{2,3}|und)$")
_CONF_RE = re.compile(r"^(0(\.[0-9]+)?|1(\.0+)?)$")
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_SNAP_RE = re.compile(r"^[0-9]{4}-([0-9]{2}|W[0-9]{2})$")


class AnnotationsError(CodexError):
    exit_code = 2


def edge_type(claim_type: str) -> str:
    """09 §9.14's `<plugin>:<name>`: `annotations:same-work`."""
    return f"{PLUGIN}:{claim_type}"


def is_annotations(codex: Any) -> bool:
    return cx.is_annotations(codex)


# --------------------------------------------------------------------------
# claims

def _nfc(obj: Any) -> Any:
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, list):
        return [_nfc(v) for v in obj]
    if isinstance(obj, dict):
        return {_nfc(k): _nfc(v) for k, v in obj.items()}
    return obj


def _plugins(target_plugin: str | None, target_plugins: Sequence[str] | None) -> tuple[str, ...]:
    if target_plugins:
        return tuple(target_plugins)
    return (target_plugin,) if target_plugin else ()


def _prefix(uid: str) -> str:
    return uid.split(":", 1)[0]


def entity_uid(canonical: str) -> str:
    """The record uid of an entity end: `wikidata:Q42` → `wikidata:42`
    through the plugin's id rule (app/plugins/wikidata/ids.py); any other
    namespace's canonical id is its record's uid as written. The namespace
    of an entity end is the plugin of the target that holds it: the
    reference source registering the namespace is that plugin's (06 §6.5)."""
    ns, _, ident = canonical.partition(":")
    if ns == "wikidata":
        from app.plugins.wikidata import ids
        return f"{ns}:{ids.native_id_of(ident)}"
    return canonical


def _entity_ok(ns: str, ident: str, *, property_: bool = False) -> bool:
    if ns == "wikidata":
        from app.plugins.wikidata import ids
        return ids.is_entity_id(ident) and (ids.kind_of(ident) == "property") == property_
    return bool(ident)


def _check_uid(v: Any, where: str, plugins: tuple[str, ...]) -> str:
    if not isinstance(v, str) or ":" not in v or not v.split(":", 1)[1]:
        raise AnnotationsError(f"{where} is a target record's uid <plugin>:<native_id>, got {v!r}")
    if plugins and _prefix(v) not in plugins:
        raise AnnotationsError(f"{where} {v!r} is not a uid of the target, whose plugin "
                               f"is {' or '.join(repr(p) for p in plugins)}")
    return v


def _check_entity(v: Any, where: str, plugins: tuple[str, ...], *, property_: bool = False) -> str:
    what = "a property's" if property_ else "an entity's"
    if not isinstance(v, str) or ":" not in v:
        raise AnnotationsError(f"{where} is {what} canonical id <namespace>:<id> (09 §9.14), got {v!r}")
    ns, _, ident = v.partition(":")
    if plugins and ns not in plugins:
        raise AnnotationsError(f"{where} {v!r} is not in a target's namespace, which is "
                               f"{' or '.join(repr(p) for p in plugins)}")
    if not _entity_ok(ns, ident, property_=property_):
        raise AnnotationsError(f"{where} {v!r} is not {what} id in the {ns!r} namespace")
    return v


def _check_chunk_end(v: Any, where: str, plugins: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(v, dict) or tuple(sorted(v)) != tuple(sorted(CHUNK_END_KEYS)):
        raise AnnotationsError(f"{where} is a chunk of the text target, {{{', '.join(CHUNK_END_KEYS)}}} "
                               f"(09 §9.5, amended 2026-10-20), got {v!r}")
    _check_uid(v["uid"], f"{where}.uid", plugins)
    if not isinstance(v["chunk_id"], str) or not v["chunk_id"]:
        raise AnnotationsError(f"{where}.chunk_id is the chunk's id as the target's chunker names it")
    if not isinstance(v["text_hash"], str) or not _HEX32_RE.match(v["text_hash"]):
        raise AnnotationsError(f"{where}.text_hash is the BLAKE2b-128 of the chunk's text, 32 hex digits")
    return v


def _check_int(v: Any, where: str, *, minimum: int = 0) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
        raise AnnotationsError(f"{where} is an integer of at least {minimum}, got {v!r}")
    return v


def check_claim(c: Any, where: str = "claim", *, target_plugin: str | None = None,
                target_plugins: Sequence[str] | None = None) -> dict[str, Any]:
    """One claim, refused with its reason or returned in its canonical form
    (strings NFC; no float anywhere, since a claim is hashed — 09 §9.1).
    `target_plugins` are the plugins of the collections annotated, in which
    every end must be; `target_plugin` is the one-target spelling."""
    plugins = _plugins(target_plugin, target_plugins)
    if not isinstance(c, dict):
        raise AnnotationsError(f"{where}: a claim is a JSON object")
    t = c.get("type")
    if t not in CLAIM_TYPES:
        raise AnnotationsError(f"{where}: type is one of {', '.join(CLAIM_TYPES)}, got {t!r}")
    keys = COMMON_KEYS + OWN_KEYS[t]
    missing = [k for k in keys if k not in c]
    unknown = sorted(k for k in c if k not in keys + PINNED_KEYS)
    if missing or unknown:
        raise AnnotationsError(
            f"{where}: " + "; ".join(x for x in (
                f"missing {', '.join(missing)}" if missing else "",
                f"unknown {', '.join(unknown)}" if unknown else "") if x)
            + f" (a {t} claim is {', '.join(keys)}; 09 §9.5, amended "
              f"{'2026-10-09' if t in WORK_TYPES else '2026-10-20'})")
    try:
        cx._reject_floats(c, where)
    except CodexError as e:
        raise AnnotationsError(f"{where}: {e} — write a ratio as a decimal string") from None
    c = _nfc(c)       # the only change made to what is checked (`_claim_of_line` relies on it)
    if t in WORK_TYPES:
        for k in ("subject", "object"):
            _check_uid(c[k], f"{where}: {k}", plugins)
        if c["subject"] == c["object"]:
            raise AnnotationsError(f"{where}: subject and object are the same record")
        if _prefix(c["subject"]) != _prefix(c["object"]):
            raise AnnotationsError(f"{where}: a {t} claim joins two records of ONE target; "
                                   f"{c['subject']} and {c['object']} are in different ones")
        for k in ("subject_lang", "object_lang"):
            if not isinstance(c[k], str) or not _LANG_RE.match(c[k]):
                raise AnnotationsError(f"{where}: {k} is an ISO 639 code or \"und\", got {c[k]!r}")
    elif t == MENTION:
        _check_chunk_end(c["subject"], f"{where}: subject", plugins)
        _check_entity(c["object"], f"{where}: object", plugins)
        start, end = _check_int(c["start"], f"{where}: start"), _check_int(c["end"], f"{where}: end")
        if end <= start:
            raise AnnotationsError(f"{where}: end is after start (code points in the chunk's text, end "
                                   f"exclusive), got {start}..{end}")
        if not isinstance(c["surface"], str) or not c["surface"]:
            raise AnnotationsError(f"{where}: surface is the text between start and end")
    elif t == RELATION:
        _check_entity(c["subject"], f"{where}: subject", plugins)
        _check_entity(c["object"], f"{where}: object", plugins)
        if c["subject"] == c["object"]:
            raise AnnotationsError(f"{where}: subject and object are the same entity")
        _check_entity(c["relation"], f"{where}: relation", plugins, property_=True)
        _check_chunk_end(c["evidence_chunk"], f"{where}: evidence_chunk", plugins)
    else:                                                   # COMMUNITY
        sub = c["subject"]
        if not isinstance(sub, dict) or set(sub) != {"id", "level"} or not isinstance(sub["id"], str) \
                or not sub["id"]:
            raise AnnotationsError(f"{where}: a community's subject is {{id, level}}, the id the method "
                                   f"mints, got {sub!r}")
        _check_int(sub["level"], f"{where}: subject.level")
        if c["object"] is not None:
            raise AnnotationsError(f"{where}: a community has no object; it is null")
        members = c["members"]
        if not isinstance(members, list) or len(members) < 2:
            raise AnnotationsError(f"{where}: members is a list of at least two entities")
        for i, m in enumerate(members):
            _check_entity(m, f"{where}: members[{i}]", plugins)
        if members != sorted(members, key=lambda m: m.encode("utf-8")) or len(set(members)) != len(members):
            raise AnnotationsError(f"{where}: members are sorted by bytes, no entity twice")
        if not isinstance(c["summary"], str):
            raise AnnotationsError(f"{where}: summary is the method's text about the members")
    m = c["method"]
    if not isinstance(m, dict) or m.get("kind") not in METHOD_KINDS:
        raise AnnotationsError(f"{where}: method is {{kind: {' | '.join(METHOD_KINDS)}, …}}")
    if m["kind"] == "matcher":
        if set(m) != {"kind", "id", "version"} or not isinstance(m["id"], str) or not m["id"] \
                or not isinstance(m["version"], int) or isinstance(m["version"], bool):
            raise AnnotationsError(f"{where}: a matcher method is {{kind, id, version}}, the "
                                   f"version an integer — what a verifier re-runs")
    elif m["kind"] == "model":
        if (set(m) != set(MODEL_METHOD_KEYS) or not isinstance(m["id"], str) or not m["id"]
                or not isinstance(m["version"], int) or isinstance(m["version"], bool)
                or not all(isinstance(m[k], str) and cx._DIGEST_RE.match(m[k]) for k in ("model", "prompt"))
                or m["temperature"] != "0"):
            raise AnnotationsError(
                f"{where}: a model method is {{{', '.join(MODEL_METHOD_KEYS)}}}: the version an integer, "
                f"model and prompt sha256: digests of the weights and the prompt text, temperature \"0\" "
                f"— what the model-agreement check re-runs (02 §2.10, 03 §3.2)")
    elif set(m) != {"kind"}:
        raise AnnotationsError(f"{where}: a {m['kind']} method is {{kind}} alone; what it saw "
                               f"goes in `evidence`")
    if not isinstance(c["evidence"], dict):
        raise AnnotationsError(f"{where}: evidence is an object — what the method matched on")
    if not isinstance(c["confidence"], str) or not _CONF_RE.match(c["confidence"]):
        raise AnnotationsError(f"{where}: confidence is a decimal string from 0 to 1, got "
                               f"{c['confidence']!r}")
    if not isinstance(c["contributor"], str) or not c["contributor"].strip():
        raise AnnotationsError(f"{where}: contributor names who makes the claim")
    for k in ("subject_hash", "object_hash"):
        if k in c and c[k] is None and t == COMMUNITY:
            continue                        # a community pins no record (09 §9.5)
        if k in c and not (isinstance(c[k], str) and _HEX32_RE.match(c[k])):
            raise AnnotationsError(f"{where}: {k} is a content_hash, 32 lowercase hex digits")
    if "target_snapshot" in c and not (isinstance(c["target_snapshot"], str)
                                       and _SNAP_RE.match(c["target_snapshot"])):
        raise AnnotationsError(f"{where}: target_snapshot is a snapshot id")
    return c


@dataclass(frozen=True)
class Submitted:
    """One claim as the claims file gave it: its line number, its bytes
    (what `origin_hash` covers) and the claim."""
    line: int
    raw: bytes
    claim: dict[str, Any]


def identity_key(c: dict[str, Any]) -> tuple:
    """What makes two claims the same claim (09 §9.5): the pair and the type
    for the 2026-10-09 types — either way round for `same-work` — and the
    type with its ends and own identifying keys for the rest."""
    t = c["type"]
    if t == SAME_WORK:                    # symmetric: one claim per pair
        return (t,) + tuple(sorted((c["subject"], c["object"])))
    if t == TRANSLATION_OF:
        return (t, c["subject"], c["object"])
    if t == MENTION:
        return (t, cx.canonical_json(c["subject"]), c["object"], c["start"], c["end"])
    if t == RELATION:
        return (t, c["subject"], c["object"], c["relation"])
    return (t, c["subject"]["id"], c["subject"]["level"])


_pair_key = identity_key                    # the 2026-10-09 name


def identity_tuple(c: dict[str, Any]) -> list:
    """What a claim is NUMBERED by at Codex GRAPH_VERSION (09 §9.5): the
    canonical JSON of this list, in byte order — (subject, object, type) for
    the two 2026-10-09 types, as before them."""
    t = c["type"]
    if t in WORK_TYPES:
        return [c["subject"], c["object"], t]
    if t == MENTION:
        return [t, c["subject"], c["object"], c["start"], c["end"]]
    if t == RELATION:
        return [t, c["subject"], c["object"], c["relation"]]
    return [t, c["subject"]["id"], c["subject"]["level"]]


def ends_of(c: dict[str, Any]) -> list[tuple[str, Any]]:
    """Every end of a claim with its kind — `(RECORD, uid)`, `(CHUNK, {uid,
    chunk_id, text_hash})`, `(ENTITY, canonical id)` — the first two being
    what `subject_hash` and `object_hash` pin (a relation's evidence chunk
    is third: checked, not pinned)."""
    t = c["type"]
    if t in WORK_TYPES:
        return [(RECORD, c["subject"]), (RECORD, c["object"])]
    if t == MENTION:
        return [(CHUNK, c["subject"]), (ENTITY, c["object"])]
    if t == RELATION:
        return [(ENTITY, c["subject"]), (ENTITY, c["object"]), (CHUNK, c["evidence_chunk"])]
    return [(ENTITY, m) for m in c["members"]]


def end_uid(kind: str, value: Any) -> str:
    """The RECORD uid an end names, in the target that holds it."""
    if kind == RECORD:
        return value
    if kind == CHUNK:
        return value["uid"]
    return entity_uid(value)


def record_ends(c: dict[str, Any]) -> list[str]:
    """The record uids a claim names, each once, in end order."""
    out: list[str] = []
    for kind, value in ends_of(c):
        uid = end_uid(kind, value)
        if uid not in out:
            out.append(uid)
    return out


def read_claims(data: bytes, *, target_plugin: str | None = None,
                target_plugins: Sequence[str] | None = None) -> list[Submitted]:
    """The claims file: JSON Lines, one claim per line, blank lines skipped.
    A claim made twice — the same pair, either way round for `same-work`;
    the same identifying keys for the other types — is refused, naming both
    lines."""
    out: list[Submitted] = []
    seen: dict[tuple, int] = {}
    for i, raw in enumerate(data.split(b"\n"), 1):
        raw = raw[:-1] if raw.endswith(b"\r") else raw
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError as e:
            raise AnnotationsError(f"claims line {i}: not JSON ({e})") from None
        c = check_claim(obj, f"claims line {i}", target_plugin=target_plugin, target_plugins=target_plugins)
        k = identity_key(c)
        if k in seen:
            raise AnnotationsError(f"claims line {i}: the same claim as line {seen[k]}")
        seen[k] = i
        out.append(Submitted(i, raw, c))
    if not out:
        raise AnnotationsError("the claims file holds no claim")
    return out


def _claim_of_line(raw: bytes) -> dict[str, Any]:
    """A claims line `check_claim` has passed, in the form it returned: the
    JSON with its strings NFC (`check_claim` checks and returns `_nfc` of
    what it was given, unchanged). What the streaming create re-reads a line
    as (app/hold/annotations_stream.py)."""
    return _nfc(json.loads(raw))


# --------------------------------------------------------------------------
# the target, as the snapshot the claims are checked against holds it

def _target_snapshot(shard_tree: Path, snapshot_id: str):
    from . import shard as shard_mod, snapshot as sn
    p = Path(shard_tree) / shard_mod.snapshot_path(snapshot_id)
    if not p.is_file():
        raise AnnotationsError(
            f"the target holds no snapshot {snapshot_id} ({p}): a claim is checked against "
            f"the records a snapshot this peer holds names (02 §2.10)")
    return sn.load(p)


def _part_file(shard_tree: Path, shard, current_id: str | None, snapshot_id: str) -> Path | None:
    """The file holding `shard` as `snapshot_id` lists it: the part's own
    path when that snapshot is the current one, else whichever version —
    current or superseded (09 §9.5) — has the listed root."""
    from . import shard as shard_mod
    newest = Path(shard_tree) / shard.path
    if current_id == snapshot_id:
        return newest if newest.is_file() else None
    suffix = shard_mod.PART_SUFFIX[shard.container]
    cands = [newest] + sorted(newest.parent.glob(f"{shard.n:05d}.*{suffix}"))
    for p in cands:
        if p.is_file() and shard_mod.pieces_root_of(p) == shard.root:
            return p
    return None


def target_records(shard_tree: Path, target: Codex, snapshot_id: str,
                   uids: Iterable[str], *, text_for: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    """uid -> {content_hash, lang} for every uid the target snapshot holds —
    with `uid`, `native_id`, `title` and `sections` for the uids in
    `text_for`, the records a chunk end names, which `pin` re-cuts. Each
    part is read once and only the frames that hold a wanted uid are
    decompressed (09 §9.5's index), so the cost is the claims', never the
    target's size."""
    text_for = set(text_for)
    from app.pack import records as pack_records
    from app.share import seeder
    snap = _target_snapshot(shard_tree, snapshot_id)
    current = seeder.current_snapshot(Path(shard_tree))
    current_id = current.snapshot_id if current is not None else None
    order = (target.slicing or {}).get("order", "bytes")
    by_part: dict[tuple[str, int], tuple[Any, list[str]]] = {}
    for uid in sorted(set(uids)):
        for s in snap.shards_of(uid.split(":", 1)[1], order):
            by_part.setdefault((s.slice, s.n), (s, []))[1].append(uid)
    out: dict[str, dict[str, Any]] = {}
    for (_, _), (shard, wanted) in sorted(by_part.items()):
        path = _part_file(Path(shard_tree), shard, current_id, snapshot_id)
        if path is None:
            continue
        index = pack_records.read_index(path)
        frames: dict[int, list[tuple[str, int]]] = {}
        for uid in wanted:
            loc = index.uids.get(uid)
            if loc is not None:
                frames.setdefault(loc[0], []).append((uid, loc[1]))
        for k, items in sorted(frames.items()):
            lines = pack_records.read_frame(path, index, k)
            for uid, line in items:
                r = json.loads(lines[line])
                out[uid] = {"content_hash": r["content_hash"], "lang": r.get("lang")}
                if uid in text_for:
                    out[uid].update(uid=r["uid"], native_id=r["native_id"], title=r.get("title"),
                                    sections=r.get("sections"))
    return out


def chunks_of(codex: Codex, record: dict[str, Any]) -> dict[str, str]:
    """chunk id -> text, as the target's chunker cuts the record — the one
    way a stage cuts a document (`layers.chunks_for_document`, invariant 1),
    a pure function of the text (invariant 3), so a chunk end is checked
    against the text this peer holds and never against a vectors table."""
    from app import layers
    row = {"uid": record["uid"], "native_id": record["native_id"]}
    sections = [tuple(x) for x in record.get("sections") or []]
    return {c["id"]: c["text"] for c in layers.chunks_for_document(codex, row, sections, title=record.get("title"))}


def _check_chunk(end: dict[str, Any], record: dict[str, Any], codices: dict[str, Codex] | None,
                 line: int) -> str:
    """The chunk end against the record this peer holds: re-cut with the
    target's chunker, the chunk exists and its text hashes to `text_hash`.
    The chunk's text, for the mention's `surface`."""
    uid = end["uid"]
    codex = (codices or {}).get(_prefix(uid))
    if codex is None or record.get("sections") is None:
        raise AnnotationsError(
            f"claims line {line}: the chunk {end['chunk_id']} cannot be checked — this peer holds no "
            f"text of {uid} to re-cut (a chunk end is checked against the target's text, 02 §2.10)")
    text = chunks_of(codex, record).get(end["chunk_id"])
    if text is None:
        raise AnnotationsError(
            f"claims line {line}: {uid} has no chunk {end['chunk_id']} under the target's chunker "
            f"(the same text cuts the same chunks, invariant 3): the claim was made about other text "
            f"or another cut")
    if blake128(text.encode("utf-8")) != end["text_hash"]:
        raise AnnotationsError(
            f"claims line {line}: text_hash {end['text_hash']} is not chunk {end['chunk_id']}'s "
            f"({blake128(text.encode('utf-8'))}): the claim was made about other text")
    return text


def _snapshot_for(uid: str, snapshot_id: str | None, snapshots: dict[str, str] | None) -> str:
    if snapshots:
        try:
            return snapshots[_prefix(uid)]
        except KeyError:
            raise AnnotationsError(f"{uid} is in no target's plugin ({', '.join(sorted(snapshots))})") from None
    if snapshot_id is None:
        raise AnnotationsError("pin: a target snapshot is needed")
    return snapshot_id


def pin(submitted: Sequence[Submitted], held: dict[str, dict[str, Any]],
        snapshot_id: str | None = None, *, snapshots: dict[str, str] | None = None,
        codices: dict[str, Codex] | None = None) -> list[dict[str, Any]]:
    """Each claim with the target records' hashes and the snapshot it was
    checked against — the snapshot of the target its SUBJECT belongs to
    (09 §9.5): `snapshot_id` with one target, `snapshots` (plugin -> target
    snapshot) with several; `codices` (plugin -> Codex) re-cuts a chunk end's
    record. A record the snapshot does not hold, a stated hash that is not
    the record's there, a chunk that is not the record's — each refuses the
    file: a claim about text we do not hold, or about other text, is not one
    we publish. A community pins no record: both hashes null."""
    out, missing = [], []
    for s in submitted:
        c = _pin_one(s, held, snapshot_id, snapshots, codices, missing)
        if c is not None:
            out.append(c)
    if missing:
        raise _not_held(len(missing), missing[:8])
    return out


def _not_held(count: int, first: Sequence[str]) -> AnnotationsError:
    """`pin`'s refusal of the ends the target snapshot does not hold: how
    many, and the first eight in the file's order."""
    return AnnotationsError(
        f"{count} claim end(s) name a record the target snapshot does "
        f"not hold: {'; '.join(first[:8])}{' …' if count > 8 else ''}")


def _pin_one(s: Submitted, held, snapshot_id: str | None, snapshots: dict[str, str] | None,
             codices: dict[str, Codex] | None, missing) -> dict[str, Any] | None:
    """One claim pinned (`pin`): the pinned copy, or None when an end is not
    held — named in `missing` (anything with `append`) — and any other
    refusal raised at once. `held.get(uid)` is a record as `target_records`
    gives it (app/hold/annotations_stream.py `Held` for a streaming create)."""
    c = dict(s.claim)
    t = c["type"]
    ends = ends_of(c)
    snap = _snapshot_for(end_uid(*ends[0]), snapshot_id, snapshots)
    hashes: list[str] = []
    lost = False
    for kind, value in ends:
        uid = end_uid(kind, value)
        got = held.get(uid)
        if got is None:
            missing.append(f"line {s.line}: {uid}" + (f" ({value})" if kind == ENTITY and value != uid else ""))
            lost = True
            continue
        if kind == CHUNK:
            text = _check_chunk(value, got, codices, s.line)
            if t == MENTION:
                if c["end"] > len(text) or text[c["start"]:c["end"]] != c["surface"]:
                    raise AnnotationsError(
                        f"claims line {s.line}: surface {c['surface']!r} is not the chunk's text at "
                        f"{c['start']}..{c['end']} ({text[c['start']:c['end']]!r})")
        hashes.append(got["content_hash"])
    if lost:
        return None
    if t == COMMUNITY:
        for k in ("subject_hash", "object_hash"):
            if c.get(k) is not None:
                raise AnnotationsError(f"claims line {s.line}: a community pins no {k} — it has no "
                                       f"record at either end (09 §9.5)")
            c[k] = None
    else:
        for k, h in zip(("subject", "object"), hashes):
            stated = c.get(f"{k}_hash")
            if stated is not None and stated != h:
                raise AnnotationsError(
                    f"claims line {s.line}: {k}_hash {stated} is not {end_uid(*ends[0 if k == 'subject' else 1])}'s "
                    f"content_hash in snapshot {snap} ({h}): the claim was checked against other text")
            c[f"{k}_hash"] = h
    if c.get("target_snapshot", snap) != snap:
        raise AnnotationsError(f"claims line {s.line}: checked against {c['target_snapshot']}, "
                               f"and these are pinned to {snap}")
    c["target_snapshot"] = snap
    return c


# --------------------------------------------------------------------------
# records and edges (09 §9.5, §9.14)

def blake128(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def claim_text(claim: dict[str, Any]) -> str:
    """The claim's one section: its canonical JSON (09 §9.1)."""
    return cx.canonical_json(claim).decode("utf-8")


def claim_of(record: dict[str, Any]) -> dict[str, Any]:
    """The claim a record line carries."""
    return json.loads(record["sections"][0][1])


def records_of(claims: Sequence[dict[str, Any]], submitted: Sequence[Submitted], *,
               version: int = cx.ANNOTATIONS_VERSION) -> list[dict[str, Any]]:
    """The claim records, numbered from 1 in (subject, object, type) byte
    order — a rule, so two annotators building the same file cut the same
    records — each with its §9.5 hashes. At GRAPH_VERSION the order is the
    byte order of each claim's `identity_tuple` as canonical JSON (09 §9.5,
    amended 2026-10-20), which is the same order for the 2026-10-09 types
    whose uids carry no byte below `"`; the version-7 rule is kept as it was
    so no part already written moves."""
    pairs = sorted(zip(claims, submitted), key=lambda cs: _number_order(cs[0], version))
    return [_record(n, c, s) for n, (c, s) in enumerate(pairs, 1)]


def _number_order(c: dict[str, Any], version: int):
    """The byte order claims are numbered in (`records_of`)."""
    if version >= cx.GRAPH_VERSION:
        return cx.canonical_json(identity_tuple(c))
    return (c["subject"].encode(), c["object"].encode(), c["type"].encode())


def _record(n: int, c: dict[str, Any], s: Submitted) -> dict[str, Any]:
    """Claim `n` as its 09 §9.5 record line."""
    from . import records as rec
    sections = [["claim", claim_text(c)]]
    return {"uid": f"{PLUGIN}:{n}", "native_id": str(n), "lang": "und", "title": "",
            "creators": [], "date": None, "subjects": [], "keys": [],
            "sections": sections, "extra": {}, "origin": f"{CLAIMS_FILE}#{s.line}",
            "content_hash": rec.content_hash("", [tuple(x) for x in sections]),
            "origin_hash": blake128(s.raw)}


def edge_lines(records: Iterable[dict[str, Any]], snapshot_id: str) -> list[bytes]:
    """The relations of a part: each claim as its edge subject → object,
    typed `annotations:<type>`, sorted by (from, type, to) bytes (09 §9.14).
    Amended 2026-10-20: a mention runs from the chunk's RECORD to the
    entity's canonical id; a relation from the subject entity's record uid
    to the object's canonical id (the property stays in the claim); a
    community has no edge of its own — each member's record points at the
    community claim, `annotations:member-of`."""
    from . import records as rec
    edges = []
    for r in records:
        c = claim_of(r)
        t = c["type"]
        if t in WORK_TYPES:
            edges.append((c["subject"], edge_type(t), c["object"]))
        elif t == MENTION:
            edges.append((c["subject"]["uid"], edge_type(t), c["object"]))
        elif t == RELATION:
            edges.append((entity_uid(c["subject"]), edge_type(t), c["object"]))
        else:
            edges.extend((entity_uid(m), edge_type(MEMBER_OF), r["uid"]) for m in c["members"])
    edges.sort(key=lambda e: (e[0].encode(), e[1].encode(), e[2].encode()))
    return [rec.edge_line(f, t, ty, snapshot_id) for f, ty, t in edges]


# --------------------------------------------------------------------------
# the Codex

def build_codex(target: Codex | None, from_snapshot: str | None, *, title: str, summary: str,
                annotator: str, platform_image: str, nonce: str | None = None,
                attribution: str | None = None, erasure: str = "retractable",
                targets: Sequence[tuple[Codex, str]] | None = None, container: str = cx.JSONL,
                graph: bool = False) -> Codex:
    """02 §2.10's Codex: every field present, the space and work null,
    no layers, the target(s) and the snapshot the claims hold from. Version
    7 for one target, JSONL parts and the two 2026-10-09 claim types; the
    GRAPH_VERSION list where there are several targets, a Parquet container
    or a graph claim type (`graph`) — 09 §9.3's version rule, so the
    collections already written keep their bytes."""
    pairs = tuple(targets) if targets else ((target, from_snapshot),)
    cx.refuse_unbuilt_container(container)
    version = (cx.GRAPH_VERSION if len(pairs) > 1 or container != cx.JSONL or graph
               else cx.ANNOTATIONS_VERSION)
    slicing: dict[str, Any] = {"keys": [], "unknown": SLICE, "shard_records": SHARD_RECORDS,
                               "order": "numeric"}
    if version >= cx.GRAPH_VERSION:
        slicing["container"] = container
        if container == cx.PARQUET:
            slicing["shard_records"] = SHARD_RECORDS_PARQUET
    codex = Codex(
        descriptive=cx.Descriptive(title, summary),
        plugin=PLUGIN,
        origin=cx.Origin({"source_id": PLUGIN, "nonce": nonce or secrets.token_hex(16)},
                         FORM, "file", "UTC", "record-date", None),
        toolchain=cx.Toolchain(platform_image, None),
        parser=PARSER,
        cleaning_version=1,
        protocol_version=1,
        layers=(),
        slicing=slicing,
        space=None,
        work=None,
        modality="text",
        erasure=erasure,
        validation=cx.Validation(cx.ANNOTATIONS_CHECKS, {"records": {"shards": "1"}}, ()),
        license=cx.License("green", None),
        privacy="public",
        update=cx.Update("monthly", 1, 7),
        publication="open",
        embedding="open",
        credits=(cx.Credit(cx.CORPUS, None, annotator, None,
                           attribution or f"Annotations by {annotator}", "all"),),
        facets=(),
        version=version,
        record_line=1,
        annotates=tuple(cx.Annotates(t.collection_id, snap) for t, snap in pairs),
    )
    cx.validate(codex)
    return codex


# --------------------------------------------------------------------------
# the SHARD

def write_parts(shard_tree: Path, records: Iterable[dict[str, Any]], snapshot_id: str, *,
                shard_records: int = SHARD_RECORDS, container: str = cx.JSONL):
    """Record parts and their relations parts, `shard_records` (the Codex's
    `slicing.shard_records`) per part, in the Codex's `container` (09 §9.5
    "The file": JSONL frames or Parquet), and the snapshot entries for
    both. The relations parts are always JSONL (09 §9.14). `records` may
    be a stream: a part is cut as it fills, and only its records are held."""
    import itertools
    shards, relations = [], []
    for n, part in enumerate(itertools.batched(records, shard_records), 1):
        s, r = _write_part(Path(shard_tree), n, part, snapshot_id, container)
        shards.append(s)
        relations.append(r)
    return tuple(shards), tuple(relations)


def _write_part(shard_tree: Path, n: int, part: Sequence[dict[str, Any]], snapshot_id: str, container: str):
    """Part `n` of `write_parts` — its records and its edges — and the
    snapshot's entries for both; what a streamed create's pool child writes
    (app/hold/annotations_stream.py `write_part`)."""
    from app.pack import records as pack_records
    from . import shard as shard_mod, records as rec, snapshot as sn
    p = Path(shard_tree) / shard_mod.shard_path(SLICE, n, container=container)
    pack_records.write_shard(p, part, record_line=1)
    raw = p.read_bytes()
    shard = sn.Shard(
        slice=SLICE, n=n, root=shard_mod.pieces_root(raw), container=container,
        records_root=rec.records_root(r["content_hash"] for r in part),
        first_id=part[0]["native_id"], last_id=part[-1]["native_id"],
        records=len(part), chunks=0, chunks_root=rec.chunks_root([]), tokens=0,
        bytes=len(raw), toolchain="own")
    lines = edge_lines(part, snapshot_id)
    rp = Path(shard_tree) / shard_mod.relations_path(SLICE, n)
    pack_records.write_lines(rp, lines, key_of=lambda line: json.loads(line)["from"])
    rraw = rp.read_bytes()
    return shard, sn.RelationShard(slice=SLICE, n=n, root=shard_mod.pieces_root(rraw),
                                   edges=len(lines), bytes=len(rraw))


def files_of(shard_tree: Path, snapshot_id: str) -> dict[str, Any]:
    """09 §9.4's `files` for an annotations collection: `validation.json`,
    `LICENSE-TERMS` (null: green) and the seals file (null: open). It has
    no calibration member — no space (09 §9.4, amended 2026-10-09)."""
    from . import shard as shard_mod
    out: dict[str, Any] = {}
    for name, required in ((shard_mod.VALIDATION_PATH, True), (shard_mod.TERMS_PATH, False),
                           (shard_mod.seals_path(snapshot_id), False)):
        p = Path(shard_tree) / name
        if p.is_file():
            raw = p.read_bytes()
            out[name] = {"root": shard_mod.pieces_root(raw), "bytes": len(raw)}
        elif required:
            raise AnnotationsError(f"{name} is missing from the SHARD")
        else:
            out[name] = None
    return out


def write_snapshot(shard_tree: Path, staging: Path, codex: Codex,
                   records: Iterable[dict[str, Any]], snapshot_id: str):
    """The parts, `validation.json` and the FIRST snapshot. `records` may be
    a stream (`write_parts`), or what writes its own parts (`write_parts`
    method: a streamed create's, app/hold/annotations_stream.py); of each
    record only its uid is kept, for the delta, which lists every one as
    added.

    The parts are cut in `staging` and promoted into the SHARD only when the
    snapshot is new, so nothing a snapshot already lists is ever
    overwritten: run again with the same claims it computes the same bytes
    and is a no-op; a snapshot that exists with other bytes is refused, as
    `hold snapshot create` refuses (09 §9.4). A later snapshot — the claims
    re-checked, re-issuing parts under 09 §9.5's superseded names — is not
    built here yet, and is refused rather than half-done."""
    import os
    from . import shard as shard_mod, snapshot as sn
    shard_tree, staging = Path(shard_tree), Path(staging)
    out = shard_tree / shard_mod.snapshot_path(snapshot_id)
    previous = sn._previous(shard_tree, snapshot_id)
    if previous is not None:
        raise AnnotationsError(
            f"this collection already has snapshot {previous.snapshot_id}: a later snapshot "
            f"re-issues its parts (09 §9.5), which `create` does not do — it makes the first")
    (shard_tree / shard_mod.VALIDATION_PATH).write_bytes(cx.canonical_json(codex.validation.to_dict()))
    if staging.exists():
        shutil.rmtree(staging)
    per_part = int(codex.slicing.get("shard_records") or SHARD_RECORDS)
    if hasattr(records, "write_parts"):         # a streamed create's (annotations_stream.ClaimParts)
        shards, relations, uids = records.write_parts(staging, snapshot_id, shard_records=per_part,
                                                      container=cx.container_of(codex))
    else:
        uids = []

        def kept(rs: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
            for r in rs:
                uids.append(r["uid"])
                yield r
        shards, relations = write_parts(staging, kept(records), snapshot_id, shard_records=per_part,
                                        container=cx.container_of(codex))
    snap = sn.Snapshot(
        collection_id=codex.collection_id, snapshot_id=snapshot_id, previous=None,
        shards=shards,
        # `compute_delta(None, {uid: content_hash})`: every uid added, once, sorted
        delta=sn.Delta(_sorted_once(uids), (), ()),
        relations=relations, figures=(), files=files_of(shard_tree, snapshot_id))
    uids.clear()                                # the delta holds them now
    # the canonical bytes once: a first snapshot lists every claim in its delta
    # (12.3 M uids for wikipedia's), and `hash` re-serialises at each call
    raw = snap.canonical()
    object.__setattr__(snap, "_raw_hash", cx.digest(raw))
    if out.is_file():
        shutil.rmtree(staging)
        if out.read_bytes() == raw:
            return snap
        raise AnnotationsError(f"snapshot {snapshot_id} exists with different bytes; a snapshot "
                               f"is written once (09 §9.4)")
    for entry in list(shards) + list(relations):
        dst = shard_tree / entry.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        # the SHARD lives on other storage than `state/` since 2026-10-12
        # (shard -> /shards/<name>, tank): a rename cannot cross, and the first
        # create after the move failed with EXDEV (2026-10-19). `promote` copies
        # beside the target and renames there, whole or not at all
        shard_mod.promote(staging / entry.path, dst)
    shutil.rmtree(staging)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return snap


def _sorted_once(uids: list[str]) -> tuple[str, ...]:
    """`uids` sorted, each once (`compute_delta`'s `added` of a first
    snapshot), sorted in place: a claim's uid is its number, so a repeat
    never comes, and a set of twelve million to drop one would cost."""
    import itertools
    uids.sort()
    if any(a == b for a, b in zip(uids, itertools.islice(uids, 1, None))):
        uids[:] = sorted(set(uids))
    return tuple(uids)


def write_held_file(root: Path, codex: Codex, name: str) -> Path:
    """07 §7.8's collection file: the Codex, every slice, records and
    relations, nothing embedded (there is nothing to embed), not shared
    until the operator says so. No `[encoder]`: an annotations collection
    has no space (02 §2.10)."""
    from .deposit import held_file
    path = held_file(root, name)
    if path.exists():
        import tomllib
        with open(path, "rb") as f:
            held = str(tomllib.load(f).get("codex") or "")
        if held.removeprefix("sha256:") == codex.collection_id.removeprefix("sha256:"):
            return path
        raise AnnotationsError(f"{path} holds {held or 'no codex'}; a name holds one collection "
                               f"for its life (07 §7.8)")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(f'codex = "{codex.collection_id}"\n'
                   'slices = "all"\n'
                   'kinds = ["records", "relations"]\n'
                   'embed = "off"\n'
                   "share = false\n", encoding="utf-8")
    tmp.replace(path)
    return path


@dataclass(frozen=True)
class Target:
    """One collection an annotations collection is about, as this peer
    holds it: its local name, its Codex, the snapshot the claims hold from
    and its SHARD."""
    name: str
    codex: Codex
    from_snapshot: str
    shard_tree: Path

    @property
    def plugin(self) -> str:
        return self.codex.plugin


@dataclass(frozen=True)
class Checked:
    """What `create` checked before writing anything."""
    targets: tuple[Target, ...]
    submitted: tuple[Submitted, ...]
    pinned: tuple[dict[str, Any], ...]
    annotator: str

    @property
    def graph(self) -> bool:
        """Does any claim take a 2026-10-20 type (GRAPH_VERSION)?"""
        return any(c["type"] in GRAPH_TYPES for c in self.pinned)


def _targets(root: Path, refs: Sequence[tuple[str, str]]) -> tuple[Target, ...]:
    """The targets named, resolved and refused where 02 §2.10 refuses: an
    annotations collection, one plugin twice (a claim's end names its target
    by the uid's plugin prefix, 09 §9.3), one collection twice."""
    from . import collection as col
    out: list[Target] = []
    for ref, from_snapshot in refs:
        if not from_snapshot:
            raise AnnotationsError(f"{ref}: every target names the snapshot the claims hold from "
                                   f"(--from-snapshot once per --annotates)")
        tname, target = col.resolve(Path(root), ref)
        if is_annotations(target):
            raise AnnotationsError(f"{tname} is itself an annotations collection; annotations are "
                                   f"about a collection's records (02 §2.10)")
        for t in out:
            if t.codex.collection_id == target.collection_id:
                raise AnnotationsError(f"{tname} is named twice among the targets")
            if t.plugin == target.plugin:
                raise AnnotationsError(f"{t.name} and {tname} are both {target.plugin!r} collections: a "
                                       f"claim's end names its target by its uid's plugin prefix, so "
                                       f"one target per plugin (09 §9.3, amended 2026-10-20)")
        out.append(Target(tname, target, from_snapshot, col.shard_dir_of(Path(root), tname)))
    return tuple(out)


def newest_snapshot(root: Path, ref: str) -> tuple[str, str]:
    """(name, the newest snapshot it holds) of a held target — what `update`
    re-checks against when nothing names one, and what a create the node
    decides holds the claims from (RULE 2, 2026-10-25)."""
    from . import collection as col
    from app.share import seeder
    tname, _ = col.resolve(Path(root), ref)
    cur = seeder.current_snapshot(col.shard_dir_of(Path(root), tname))
    if cur is None:
        raise AnnotationsError(f"{tname} holds no snapshot yet, and claims are checked against one "
                               f"(02 §2.10): wait until its first snapshot is held, then create the "
                               f"annotations")
    return tname, cur.snapshot_id


def targets_of_claims(root: Path, claims: bytes | Path) -> list[tuple[str, str]]:
    """The targets a claims file names, decided by the node (RULE 2,
    amended 2026-10-25): every end's uid names its target's PLUGIN by its
    prefix (09 §9.3), and the target is the collection of that plugin held
    here — the one whose newest snapshot holds every record the claims name
    where several are held — with that newest snapshot as `from_snapshot`.
    Where no held collection or more than one could be it, nothing is
    guessed: the refusal says which, and how to name it on the command line.
    `claims` is the file's bytes or its path; it is streamed, never held."""
    from . import annotations_stream as st, collection as col
    from app.share import seeder
    root = Path(root)
    with st.ClaimsSource(claims) as src:
        sv = st.survey(src)
    held = [(n, c) for n, c in col._held(root) if not is_annotations(c)]
    out: list[tuple[str, str]] = []
    for plugin, ends in sv.ends.items():
        cands = []
        for n, c in held:
            if c.plugin != plugin:
                continue
            tree = col.shard_dir_of(root, n)
            cur = seeder.current_snapshot(tree)
            if cur is not None:
                cands.append((n, c, cur.snapshot_id, tree))
        example = ends.first
        # a directory whose file is gone is retired, not held (07 §7.8: the
        # file existing IS the collection existing) — where any has its file
        from app import collections as collections_mod
        filed = [t for t in cands if collections_mod.config_toml_path(t[0]).exists()]
        cands = filed or cands
        if not cands:
            raise AnnotationsError(
                f"the claims name {plugin} records ({example}…), and this node holds no {plugin} "
                f"collection with a snapshot: take or create one, then create the annotations")
        if len(cands) > 1:
            cands = [t for t in cands
                     if st.read_target(Target(t[0], t[1], t[2], t[3]), ends).held == len(ends.digests)]
        if len(cands) != 1:
            names = ", ".join(sorted(t[0] for t in cands)) or "none of them"
            raise AnnotationsError(
                f"the claims name {plugin} records, and this node holds several {plugin} "
                f"collections that could be their target ({names}): nothing is guessed — name it on "
                f"the command line: `louise hold collection create annotations --annotates <collection> "
                f"--claims FILE --name <name>`")
        out.append((cands[0][0], cands[0][2]))
    return out


def resolve_targets(root: Path, claims: bytes | Path, annotates: Any = None,
                    from_snapshot: Any = None) -> list[tuple[str, str]]:
    """(collection, from_snapshot) pairs for a create: as named (the command
    line's `--annotates` / `--from-snapshot`, one per target), each target's
    newest held snapshot where none is named, and decided from the claims
    where no target is named (`targets_of_claims`)."""
    def items(v: Any) -> list[str]:
        parts = [] if v is None else (v if isinstance(v, (list, tuple)) else [v])
        return [x.strip() for p in parts for x in str(p).split(",") if x.strip()]
    refs, snaps = items(annotates), items(from_snapshot)
    if not refs:
        if snaps:
            raise AnnotationsError("--from-snapshot names the snapshot of a target --annotates names")
        return targets_of_claims(root, claims)
    if not snaps:
        return [newest_snapshot(root, r) for r in refs]
    if len(refs) != len(snaps):
        raise AnnotationsError(f"create annotations names {len(refs)} collection(s) with --annotates and "
                               f"{len(snaps)} snapshot(s) with --from-snapshot: one --from-snapshot per "
                               f"--annotates, in the same order (02 §2.10)")
    return list(zip(refs, snaps))


def _refs(target_ref: str | None, from_snapshot: str | None,
          targets: Sequence[tuple[str, str]] | None) -> tuple[tuple[str, str], ...]:
    if targets:
        return tuple((str(r), str(s)) for r, s in targets)
    if not target_ref:
        raise AnnotationsError("create annotations names the collection(s) it annotates")
    return ((target_ref, from_snapshot or ""),)


def _checked(root: Path, target_ref: str | None = None, from_snapshot: str | None = None,
             claims: bytes = b"", *, annotator: str | None,
             targets: Sequence[tuple[str, str]] | None = None) -> Checked:
    """What `create` checks before it writes anything: the targets, the
    claims file, every claim's ends in the target snapshots — each chunk
    end re-cut — and the one annotator."""
    ts = _targets(Path(root), _refs(target_ref, from_snapshot, targets))
    by_plugin = {t.plugin: t for t in ts}
    submitted = read_claims(claims, target_plugins=tuple(by_plugin))
    wanted: dict[str, set[str]] = {}
    text_for: dict[str, set[str]] = {}
    for s_ in submitted:
        for kind, value in ends_of(s_.claim):
            uid = end_uid(kind, value)
            wanted.setdefault(_prefix(uid), set()).add(uid)
            if kind == CHUNK:
                text_for.setdefault(_prefix(uid), set()).add(uid)
    held: dict[str, dict[str, Any]] = {}
    for plugin, uids in wanted.items():
        t = by_plugin[plugin]              # read_claims saw to it that every prefix is a target's
        held.update(target_records(t.shard_tree, t.codex, t.from_snapshot, uids,
                                   text_for=text_for.get(plugin, ())))
    pinned = pin(submitted, held, snapshots={p: t.from_snapshot for p, t in by_plugin.items()},
                 codices={p: t.codex for p, t in by_plugin.items()})
    contributors = sorted({c["contributor"] for c in pinned})
    annotator = annotator or (contributors[0] if len(contributors) == 1 else None)
    if not annotator:
        raise AnnotationsError(f"the claims name {len(contributors)} contributors "
                               f"({', '.join(contributors[:5])}), and an annotations collection "
                               f"credits one annotator: give a claims file whose claims name one "
                               f"contributor, or name the annotator the Codex credits on the "
                               f"command line with --annotator")
    return Checked(ts, tuple(submitted), tuple(pinned), annotator)


@dataclass
class Streamed:
    """What the streaming check (`_checked_stream`) found before writing
    anything: the targets, the one annotator, the claims file's survey,
    the target records the claims name (`tables`: `annotations_stream.Held`,
    or the directory it was saved in for the pool's children), the numbering
    version and the claims in numbering order (`sort`, None for a preview)."""
    targets: tuple[Target, ...]
    annotator: str
    survey: Any
    tables: Any
    version: int
    sort: Any
    snapshots: dict[str, str]
    codices: dict[str, Codex]

    @property
    def graph(self) -> bool:
        return self.survey.graph

    def records(self, src):
        """The claim records in numbering order, read back from `src`, as
        `write_snapshot` writes them (`annotations_stream.ClaimParts`)."""
        from . import annotations_stream as st
        parts = st.ClaimParts(src, self.survey, self.sort, self.tables, self.snapshots, self.codices)
        self.survey = self.tables = self.sort = None     # the parts hold what they need
        return parts


def _version_for(targets: Sequence[Target], container: str, graph: bool) -> int:
    """`build_codex`'s version rule: 7 for one target, JSONL parts and the
    2026-10-09 claim types; GRAPH_VERSION otherwise."""
    return (cx.GRAPH_VERSION if len(targets) > 1 or container != cx.JSONL or graph
            else cx.ANNOTATIONS_VERSION)


def _checked_stream(ts: tuple[Target, ...], src, *, annotator: str | None, container: str,
                    work: Path | None, sort: bool = False) -> Streamed:
    """`_checked`, streaming (app/hold/annotations_stream.py), for the
    targets `ts` (`_targets`, the first check): the same checks in the same
    order, so the same refusal — the claims file, every claim's ends in the
    target snapshots, the one annotator — with nothing of the claims held but
    their offsets and the digests of their ends; the targets' tables saved in
    the workspace `work` for the pool's children (none: read here, for a file
    given as bytes). With `sort`, the claims are also put in numbering order
    there (`records_of`'s), for `Streamed.records`."""
    from . import annotations_stream as st
    by_plugin = {t.plugin: t for t in ts}
    sv = st.survey(src, plugins=tuple(by_plugin),
                   orders={p: (t.codex.slicing or {}).get("order", "bytes") for p, t in by_plugin.items()})
    # survey saw to it that every prefix is a target's
    work = work if src.data is None else None
    held = st.Held({p: st.read_target(by_plugin[p], ends, work) for p, ends in sv.ends.items()})
    for ends in sv.ends.values():
        ends.summaries = None                       # read: the parts are found
    tables = held if work is None else held.save(work)
    del held
    version = _version_for(ts, container, sv.graph)
    snapshots = {p: t.from_snapshot for p, t in by_plugin.items()}
    codices = {p: t.codex for p, t in by_plugin.items()}
    order = st.ExternalSort(work) if sort and work is not None else None
    st.check_pins(src, sv, tables, snapshots=snapshots, codices=codices, sort=order, version=version)
    contributors = sorted(sv.contributors)
    annotator = annotator or (contributors[0] if len(contributors) == 1 else None)
    if not annotator:
        raise AnnotationsError(f"the claims name {len(contributors)} contributors "
                               f"({', '.join(contributors[:5])}), and an annotations collection "
                               f"credits one annotator: give a claims file whose claims name one "
                               f"contributor, or name the annotator the Codex credits on the "
                               f"command line with --annotator")
    return Streamed(ts, annotator, sv, tables, version, order, snapshots, codices)


def _method_name(m: dict[str, Any]) -> str:
    return m["kind"] + (f":{m['id']}" if m["kind"] in ("matcher", "model") else "")


def preview(root: Path, *, target_ref: str | None = None, from_snapshot: str | None = None,
            claims: bytes | Path, annotator: str | None = None,
            targets: Sequence[tuple[str, str]] | None = None, container: str = cx.JSONL) -> dict[str, Any]:
    """`create annotations --dry-run`: the claims checked against the target
    snapshot(s) exactly as `create` checks them, counted, nothing written —
    for a file of thousands of claims about a live collection, the measure
    before the write (operator, 2026-10-19: the wiki-gutenberg collection).
    `target`, `target_id` and `from_snapshot` are the first target's;
    `targets` lists them all. `claims` is the file's bytes or its path,
    streamed (`create`)."""
    from . import annotations_stream as st
    cx.refuse_unbuilt_container(container)
    ts = _targets(Path(root), _refs(target_ref, from_snapshot, targets))
    st.sweep(Path(root), lambda m: print(m, file=sys.stderr))     # stdout is the preview's own
    bytes_given = isinstance(claims, (bytes, bytearray, memoryview))
    with (nullcontext() if bytes_given else st.workspace(root, f"preview.{secrets.token_hex(6)}")) as work, \
            st.ClaimsSource(claims) as src:
        try:
            ch = _checked_stream(ts, src, annotator=annotator, container=container, work=work)
        finally:
            st.forget()
    first = ch.targets[0]
    sv = ch.survey
    per_part = SHARD_RECORDS_PARQUET if container == cx.PARQUET else SHARD_RECORDS
    return {"target": first.name, "target_id": first.codex.collection_id, "from_snapshot": first.from_snapshot,
            "targets": [{"target": t.name, "collection_id": t.codex.collection_id,
                         "from_snapshot": t.from_snapshot} for t in ch.targets],
            "version": ch.version, "container": container,
            "claims": sv.count, "target_records_named": sv.records_named, "annotator": ch.annotator,
            "by_type": dict(sorted(sv.by_type.items())),
            "by_method": dict(sorted(sv.by_method.items())),
            "parts": -(-sv.count // per_part),
            "written": False}


def create(root: Path, name: str, *, target_ref: str | None = None, from_snapshot: str | None = None,
           claims: bytes | Path, platform_image: str, title: str | None = None, summary: str | None = None,
           annotator: str | None = None, snapshot_id: str | None = None,
           key=None, now: str | None = None, nonce: str | None = None,
           log=print, targets: Sequence[tuple[str, str]] | None = None,
           container: str = cx.JSONL, _in_memory: bool = False) -> tuple[Codex, Any]:
    """`hold collection create annotations` (07 §7.3): the Codex, the
    claims kept as this collection's origin, the record and relations
    parts, `validation.json`, the first snapshot (signed when this peer has
    a key) and the collection file. `targets` — (collection, from_snapshot)
    pairs, `--annotates` and `--from-snapshot` repeated — or the one
    `target_ref` / `from_snapshot`.

    The claims are checked first and the targets read before anything is
    written, so a refused file leaves nothing behind. A name already
    holding an annotations collection of the same targets, container and
    version is RESUMED (its Codex kept, every part rewritten byte for
    byte); any other holder of the name is refused.

    STREAMED (operator, 2026-11-10; docs/notes/wikipedia-one-version.md
    "Measured: the size"): `claims` — the file's bytes or its path — is
    copied once into a workspace beside the collections and read from there
    pass by pass, never held; the targets are read once; every pass runs on
    the parse pool (app/hold/annotations_stream.py). Every byte written is
    what the in-memory path, `_in_memory` — every claim held, as until
    2026-11-10, kept for that proof alone — writes
    (tests/hold/test_annotations_stream.py)."""
    from . import annotations_stream as st, collection as col
    root = Path(root)
    col.check_name(name)
    cx.refuse_unbuilt_container(container)

    def hold(codex: Codex) -> None:
        col.create(root, codex, name=name)
    if _in_memory:
        ch = _checked(root, target_ref, from_snapshot, claims, annotator=annotator, targets=targets)
        return _write_collection(
            root, name, ch.targets, ch.annotator, ch.graph, hold, container=container, title=title,
            summary=summary, platform_image=platform_image, nonce=nonce, snapshot_id=snapshot_id, key=key,
            now=now, log=log, version=None, records=lambda version: records_of(ch.pinned, ch.submitted,
                                                                               version=version),
            count=len(ch.pinned), install=lambda dst: dst.write_bytes(claims))
    ts = _targets(root, _refs(target_ref, from_snapshot, targets))
    st.sweep(root, log)
    with st.workspace(root, f"create.{name}") as work, st.private_source(claims, work) as src:
        try:
            cs = _checked_stream(ts, src, annotator=annotator, container=container, work=work, sort=True)
            return _write_collection(
                root, name, cs.targets, cs.annotator, cs.graph, hold, container=container, title=title,
                summary=summary, platform_image=platform_image, nonce=nonce, snapshot_id=snapshot_id, key=key,
                now=now, log=log, version=cs.version, records=lambda version: cs.records(src),
                count=cs.survey.count, install=src.install)
        finally:
            st.forget()


def _create_in_memory(root: Path, name: str, **kw) -> tuple[Codex, Any]:
    """`create` as it was until 2026-11-10, every claim held (`_checked`,
    `records_of`): the reference the streamed `create` is proven against,
    byte for byte (tests/hold/test_annotations_stream.py), and nothing else."""
    return create(root, name, _in_memory=True, **kw)


def _write_collection(root: Path, name: str, targets: Sequence[Target], annotator: str, graph: bool,
                      hold, *,
                      container: str, title: str | None, summary: str | None, platform_image: str,
                      nonce: str | None, snapshot_id: str | None, key, now: str | None, log,
                      version: int | None, records, count: int, install) -> tuple[Codex, Any]:
    """What `create` writes once the claims are checked: the Codex (`hold`
    makes it, or the one the name holds is resumed), the parts and the first snapshot from
    `records(the Codex's version)`, the claims file as the origin
    (`install`), the signature and the collection file. `version`, when
    given, is the numbering version the records were ordered by, which must
    be the Codex's."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from . import collection as col, snapshot as sn
    first = targets[0]
    tt = (first.codex.descriptive.title or first.name).strip()
    names = ", ".join(t.name for t in targets)
    wanted = build_codex(
        None, None, targets=[(t.codex, t.from_snapshot) for t in targets], container=container,
        graph=graph,
        title=title or f"Annotations on {tt}",
        summary=summary or (f"Claims about the records of {names} "
                            f"({first.codex.collection_id[:19]}…) from snapshot "
                            f"{first.from_snapshot} on, made by {annotator}; "
                            f"attached to its search results as facts, never ranked."),
        annotator=annotator, platform_image=platform_image, nonce=nonce)

    p = col.shard_dir_of(root, name) / col.CODEX_FILE
    if p.is_file():
        existing = cx.loads(p.read_bytes())
        if not (is_annotations(existing) and cx.targets_of(existing) == wanted.annotates
                and cx.container_of(existing) == container and existing.version == wanted.version):
            raise AnnotationsError(f"the name {name!r} already holds {existing.collection_id}; a "
                                   f"name holds one collection for its life (07 §7.8)")
        codex = existing
        log(f"[annotations] {name}: resuming {codex.collection_id[:23]}…")
    else:
        codex = wanted
        hold(codex)
    if version is not None and version != codex.version:
        raise AnnotationsError(f"the claims were ordered for version {version} and the Codex is "
                               f"{codex.version}: nothing is written")
    shard_tree = col.shard_dir_of(root, name)
    if snapshot_id is None:
        snapshot_id = sn.snapshot_id_for(datetime.now(ZoneInfo(codex.origin.clock)),
                                         codex.update.cadence)
    snap = write_snapshot(shard_tree, root / name / "state" / "annotations-build", codex, records(codex.version),
                          snapshot_id)
    # the claims as submitted: this collection's origin (02 §2.10), what each
    # record's `origin` locates and `origin_hash` covers. Beside the SHARD,
    # never in it — an origin is not a member (07 §7.8)
    origin = root / name / "origin"
    origin.mkdir(parents=True, exist_ok=True)
    install(origin / CLAIMS_FILE)
    if key is not None:
        sn.sign_snapshot(root, name, snap, key,
                         now=now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    held_path = write_held_file(root, codex, name)
    log(f"[annotations] {name}: {count} claim(s) about {names} from "
        f"{', '.join(t.from_snapshot for t in targets)}, "
        f"snapshot {snapshot_id} {snap.hash[:23]}…; held: {held_path}")
    _index_if_large(shard_tree, snap, name, log)
    return codex, snap


# --------------------------------------------------------------------------
# updating: the next snapshot of the SAME collection (operator, 2026-10-23:
# "annotations must be updated, not replaced")

def update_snapshot_id(previous_id: str, now: datetime) -> str:
    """The id an update writes: the period after the previous snapshot — a
    chain is consecutive periods of one cadence (09 §9.4) — written at the
    earliest DURING that period. An annotations collection's origin is its
    annotator, whose claims at the time of writing are the state, so no
    cutoff has to pass over them (09 §9.4, amended 2026-10-23); one update
    per period, as one snapshot per period for every collection."""
    from . import snapshot as sn
    nxt = sn.next_snapshot_id(previous_id)
    open_now = sn.next_snapshot_id(sn.snapshot_id_for(now, sn.cadence_of(previous_id)))
    if nxt > open_now:
        raise AnnotationsError(
            f"snapshot {previous_id} is this period's ({open_now} is open now): the next, {nxt}, "
            f"can be written once its period opens — one snapshot per period (09 §9.4)")
    return nxt


@dataclass(frozen=True)
class Update:
    """What `update` found, and wrote unless it was a dry run."""
    snapshot_id: str
    previous_id: str
    against: dict[str, str]          # target name -> the target snapshot re-checked against
    added: int
    changed: int
    tombstoned: int
    dropped: tuple[str, ...]         # re-check only: claims whose record the target no longer holds
    reissued: tuple[str, ...]        # part paths written anew
    snapshot: Any = None             # the Snapshot written; None on a dry run or nothing to do

    def to_dict(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "previous_id": self.previous_id,
                "against": self.against, "added": self.added, "changed": self.changed,
                "tombstoned": self.tombstoned, "dropped": list(self.dropped[:64]),
                "dropped_count": len(self.dropped), "reissued": list(self.reissued),
                "written": self.snapshot is not None,
                "snapshot_hash": self.snapshot.hash if self.snapshot is not None else None}


def _target_holder(root: Path, collection_id: str) -> tuple[str, Codex]:
    """The collection held here that answers for the target `collection_id`
    — the target itself, or its records twin (a same-records carry of it,
    02 §2.4 A1f: the claims apply to its records, and it is what the search
    reads) — the one with the newest snapshot; the target itself on a tie."""
    from . import carry, collection as col
    from app.share import seeder
    cands = []
    for n, c in col._held(Path(root)):
        if is_annotations(c):
            continue
        twin = carry.records_twin_of(Path(root) / n / "state")
        if collection_id in (c.collection_id, twin):
            cur = seeder.current_snapshot(col.shard_dir_of(Path(root), n))
            cands.append(((cur.snapshot_id if cur is not None else ""), c.collection_id == collection_id, n, c))
    if not cands:
        return col.resolve(Path(root), collection_id)
    _, _, n, c = max(cands, key=lambda x: (x[0], x[1]))
    return n, c


def _held_claims(shard_tree: Path, snap) -> dict[tuple, tuple[int, dict[str, Any]]]:
    """identity key -> (claim number, record) for every claim the snapshot
    lists — what keeps a claim's uid across updates."""
    from app.pack import records as pack_records
    out: dict[tuple, tuple[int, dict[str, Any]]] = {}
    for s in snap.shards:
        # by the ROOT the snapshot lists, never the path alone: an update
        # stopped after promoting its parts left the new bytes there, and a
        # resume that read them as the previous claims found nothing to do
        p = _part_file(Path(shard_tree), s, None, snap.snapshot_id)
        if p is None:
            raise AnnotationsError(f"{s.path}: this peer holds no file with the root snapshot "
                                   f"{snap.snapshot_id} lists ({s.root[:23]}…)")
        for r in pack_records.iter_records(p):
            out[identity_key(claim_of(r))] = (int(r["native_id"]), r)
    return out


def update(root: Path, name: str, *, claims: bytes | Path | None = None,
           against: Sequence[tuple[str, str]] | None = None, snapshot_id: str | None = None,
           key=None, now: str | None = None, log=print, dry_run: bool = False,
           clock: datetime | None = None, _in_memory: bool = False) -> Update:
    """`hold annotations update <name>` (02 §2.10, built 2026-10-23): the
    next snapshot of an annotations collection — the SAME collection, its
    Codex and id untouched, so every peer that holds it takes the new
    snapshot as it takes any (04 §4.3) rather than adopting a new one.

    The claims are checked against each target's snapshot `against` names,
    else the newest the target holds here — never one before the Codex's
    `from_snapshot` (the time rule). With `claims`, that file is the whole
    claim set from now on: a claim made before keeps its number (its uid),
    a new one takes the next, one left out is tombstoned (`withdrawn`), and
    a claim whose end the target no longer holds refuses the file, as
    `create` refuses it. Without, the claims held are RE-CHECKED: re-pinned
    to the newer target text, and a claim whose record the target no longer
    holds is tombstoned and named.

    The parts are rewritten whole and promoted; a part whose bytes moved is
    re-issued and its old version kept under its superseded name (09 §9.5),
    so the previous snapshot stays servable and checkable. The claims file
    replaced is kept as `claims.<previous id>.jsonl` beside the new one.

    STREAMED (2026-11-10, as `create`; app/hold/annotations_stream.py):
    `claims` — the file's bytes or its path — is copied once into a
    workspace (the claims held, re-checked, are hard-linked there instead)
    and read pass by pass on the parse pool; the previous snapshot's claims
    are kept as their identity digests, numbers and hashes (~40 bytes a
    claim), never as records. Every byte written, and the `Update` returned,
    are what the in-memory path, `_in_memory` — kept for that proof alone —
    writes and returns (tests/hold/test_annotations_stream.py)."""
    from . import annotations_stream as st
    plan = _update_plan(Path(root), name, against, snapshot_id, clock)
    if _in_memory:
        return _update_in_memory(plan, claims, key=key, now=now, log=log, dry_run=dry_run)
    recheck = claims is None
    st.sweep(plan.root, log)
    with st.workspace(plan.root, f"update.{plan.name}") as work:
        if recheck:
            try:
                src = st.linked_source(plan.origin_dir / CLAIMS_FILE, work)
            except FileNotFoundError:
                raise AnnotationsError(f"{plan.name} keeps no {CLAIMS_FILE} beside its SHARD to re-check: "
                                       f"give the claims file") from None
        else:
            src = st.private_source(claims, work)
        with src:
            try:
                return _update_stream(plan, src, work, recheck=recheck, key=key, now=now, log=log,
                                      dry_run=dry_run)
            finally:
                st.forget()


@dataclass
class _UpdatePlan:
    """What `update` settles before it reads a claim: the collection, its
    previous snapshot and the next one's id (with the period refusal, asked
    only of an update that writes), and the targets as this peer holds them."""
    root: Path
    name: str
    codex: Codex
    shard_tree: Path
    prev: Any
    sid: str
    period_refusal: AnnotationsError | None
    targets: tuple[Target, ...]
    against_of: dict[str, str]

    @property
    def by_plugin(self) -> dict[str, Target]:
        return {t.plugin: t for t in self.targets}

    @property
    def origin_dir(self) -> Path:
        return self.root / self.name / "origin"

    @property
    def staging(self) -> Path:
        return self.root / self.name / "state" / "annotations-build"


def _update_plan(root: Path, name: str, against: Sequence[tuple[str, str]] | None,
                 snapshot_id: str | None, clock: datetime | None) -> _UpdatePlan:
    from datetime import timezone
    from . import collection as col, snapshot as sn
    from app.share import seeder
    root = Path(root)
    name, codex = col.resolve(root, name)
    if not is_annotations(codex):
        raise AnnotationsError(f"{name} is not an annotations collection (02 §2.10)")
    shard_tree = col.shard_dir_of(root, name)
    prev = seeder.current_snapshot(shard_tree)
    if prev is None:
        raise AnnotationsError(f"{name} holds no snapshot to update: `create` makes the first")
    # the period rule is asked only of an update that has something to write:
    # the same file given twice says "nothing to update", not "next month"
    period_refusal = None
    if snapshot_id:
        sid = snapshot_id
    else:
        try:
            sid = update_snapshot_id(prev.snapshot_id, clock or datetime.now(timezone.utc))
        except AnnotationsError as e:
            sid, period_refusal = sn.next_snapshot_id(prev.snapshot_id), e
    if sid != sn.next_snapshot_id(prev.snapshot_id):
        raise AnnotationsError(f"the snapshot after {prev.snapshot_id} is {sn.next_snapshot_id(prev.snapshot_id)}, "
                               f"not {sid}: a chain is consecutive periods (09 §9.4)")

    # the targets, as this peer holds them, and the snapshot each is re-checked against
    chosen = {str(r): str(s) for r, s in (against or ())}
    ts: list[Target] = []
    against_of: dict[str, str] = {}
    for a in cx.targets_of(codex):
        tname, tcodex = _target_holder(root, a.collection_id)
        ttree = col.shard_dir_of(root, tname)
        want = chosen.pop(tname, None) or chosen.pop(a.collection_id, None)
        if want is None:
            cur = seeder.current_snapshot(ttree)
            if cur is None:
                raise AnnotationsError(f"the target {tname} holds no snapshot to check the claims against")
            want = cur.snapshot_id
        if want < a.from_snapshot:
            raise AnnotationsError(f"{tname} {want} is before {a.from_snapshot}, the snapshot these claims "
                                   f"hold from (the time rule, 02 §2.10)")
        ts.append(Target(tname, tcodex, want, ttree))
        against_of[tname] = want
    if chosen:
        raise AnnotationsError(f"--against names {', '.join(sorted(chosen))}, which this collection does "
                               f"not annotate")
    return _UpdatePlan(root, name, codex, shard_tree, prev, sid, period_refusal, tuple(ts), against_of)


def _refuse_graph(plan: _UpdatePlan, graph: bool) -> None:
    if graph and plan.codex.version < cx.GRAPH_VERSION:
        raise AnnotationsError("a mention, relation or community claim needs a Codex at the graph "
                               "version (09 §9.3): not this collection's")


_LEAVES_NONE = ("the update leaves no claim: an annotations collection holds at least one "
                "(retract it instead)")


def _update_in_memory(plan: _UpdatePlan, claims: bytes | None, *, key, now, log, dry_run: bool) -> Update:
    """`update` as it was until 2026-11-10: every claim, and every record of
    the previous snapshot, held. Kept for one purpose — the proof that the
    streamed `update` writes the same bytes and says the same
    (tests/hold/test_annotations_stream.py)."""
    from . import snapshot as sn
    name, codex, shard_tree, prev = plan.name, plan.codex, plan.shard_tree, plan.prev
    by_plugin = plan.by_plugin
    # the claims: the file given, or the ones held, re-checked
    recheck = claims is None
    if recheck:
        try:
            claims = (plan.origin_dir / CLAIMS_FILE).read_bytes()
        except FileNotFoundError:
            raise AnnotationsError(f"{name} keeps no {CLAIMS_FILE} beside its SHARD to re-check: "
                                   f"give the claims file") from None
    submitted = read_claims(claims, target_plugins=tuple(by_plugin))
    if recheck:
        # what create pinned is what is re-checked: the annotator's file as
        # written, the hashes taken anew from the newer target text
        submitted = [Submitted(s.line, s.raw, {k: v for k, v in s.claim.items() if k not in PINNED_KEYS})
                     for s in submitted]
    _refuse_graph(plan, any(s.claim["type"] in GRAPH_TYPES for s in submitted))
    wanted: dict[str, set[str]] = {}
    text_for: dict[str, set[str]] = {}
    for s_ in submitted:
        for kind, value in ends_of(s_.claim):
            uid = end_uid(kind, value)
            wanted.setdefault(_prefix(uid), set()).add(uid)
            if kind == CHUNK:
                text_for.setdefault(_prefix(uid), set()).add(uid)
    held_recs: dict[str, dict[str, Any]] = {}
    for plugin, uids in wanted.items():
        t = by_plugin[plugin]
        held_recs.update(target_records(t.shard_tree, t.codex, t.from_snapshot, uids,
                                        text_for=text_for.get(plugin, ())))
    dropped: list[str] = []
    if recheck:
        keep = []
        for s_ in submitted:
            gone = [u for u in record_ends(s_.claim) if u not in held_recs]
            if gone:
                dropped.append(f"line {s_.line}: {', '.join(gone)}")
            else:
                keep.append(s_)
        submitted = keep
    pinned = pin(submitted, held_recs, snapshots={p: t.from_snapshot for p, t in by_plugin.items()},
                 codices={p: t.codex for p, t in by_plugin.items()})

    # numbers: a claim made before keeps its own; a new one takes the next
    held = _held_claims(shard_tree, prev)
    top = max((n for n, _ in held.values()), default=0)
    fresh = sorted(((c, s) for c, s in zip(pinned, submitted) if identity_key(c) not in held),
                   key=lambda cs: _number_order(cs[0], codex.version))
    number = {identity_key(c): n for n, (c, _) in enumerate(fresh, top + 1)}
    number.update({k: n for k, (n, _) in held.items()})
    records = sorted((_record(number[identity_key(c)], c, s) for c, s in zip(pinned, submitted)),
                     key=lambda r: int(r["native_id"]))
    if not records:
        raise AnnotationsError(_LEAVES_NONE)
    before = {r["uid"]: r["content_hash"] for _, r in held.values()}
    delta = sn.compute_delta(before, {r["uid"]: r["content_hash"] for r in records})

    staging = plan.staging
    if staging.exists():
        shutil.rmtree(staging)
    shards, relations = write_parts(staging, records, plan.sid,
                                    shard_records=int(codex.slicing.get("shard_records") or SHARD_RECORDS),
                                    container=cx.container_of(codex))
    return _update_finish(plan, shards=shards, relations=relations, delta=delta, dropped=dropped,
                          install=None if recheck else (lambda dst: dst.write_bytes(claims)),
                          key=key, now=now, log=log, dry_run=dry_run)


def _update_stream(plan: _UpdatePlan, src, work: Path, *, recheck: bool, key, now, log,
                   dry_run: bool) -> Update:
    """`update`'s middle, streamed (app/hold/annotations_stream.py): the
    same checks in the same order, so the same refusals and the same bytes."""
    import numpy as np
    from . import annotations_stream as st, snapshot as sn
    codex, by_plugin = plan.codex, plan.by_plugin
    sv = st.survey(src, plugins=tuple(by_plugin), keep_idents=True,
                   orders={p: (t.codex.slicing or {}).get("order", "bytes") for p, t in by_plugin.items()})
    _refuse_graph(plan, sv.graph)
    held_recs = st.Held({p: st.read_target(by_plugin[p], ends, work) for p, ends in sv.ends.items()})
    sv.ends = {}                                    # read: the parts are found
    tables = held_recs.save(work)
    del held_recs
    snapshots = {p: t.from_snapshot for p, t in by_plugin.items()}
    codices = {p: t.codex for p, t in by_plugin.items()}
    # the claims the previous snapshot holds, matched by identity: a claim
    # made before keeps its number, a new one is numbered after the last
    held = st.held_claims(plan.shard_tree, plan.prev)
    at, hit = st._search(held.idents, sv.idents)
    sv.idents = None
    sort = st.ExternalSort(work)
    dropped = st.check_pins(src, sv, tables, snapshots=snapshots, codices=codices, sort=sort,
                            version=codex.version, strip=recheck, drop=recheck, fresh=~hit)
    if held.error is not None:
        raise AnnotationsError(held.error)
    kept = np.ones(sv.count, dtype=bool)
    kept[np.fromiter((k for k, _ in dropped), dtype=np.int64, count=len(dropped))] = False
    numbers = np.zeros(sv.count, dtype=np.int64)
    numbers[hit] = held.numbers[at[hit]]
    fresh = np.fromiter((k for _key, k in sort), dtype=np.int64)
    numbers[fresh] = np.arange(held.top + 1, held.top + 1 + len(fresh), dtype=np.int64)
    del sort
    order = np.flatnonzero(kept)
    if not len(order):
        raise AnnotationsError(_LEAVES_NONE)
    order = order[np.argsort(numbers[order], kind="stable")]
    by_number = numbers[order]
    was = hit[order]
    old = at[order][was]
    del numbers, kept, at, hit

    staging = plan.staging
    if staging.exists():
        shutil.rmtree(staging)
    parts = st.ClaimParts(src, sv, None, tables, snapshots, codices, order=order, numbers=by_number,
                          strip=recheck)
    del order
    shards, relations, _ = parts.write_parts(
        staging, plan.sid, shard_records=int(codex.slicing.get("shard_records") or SHARD_RECORDS),
        container=cx.container_of(codex))
    # `compute_delta(before, after)`: by uid, compared by content hash
    differ = (held.hashes[old] != parts.hashes[was]).any(axis=1)
    if held.odd:
        differ |= np.isin(held.numbers[old], np.fromiter(held.odd, dtype=np.int64, count=len(held.odd)))
    used = np.zeros(len(held.numbers), dtype=bool)
    used[old] = True

    def uids(ns) -> tuple[str, ...]:
        return tuple(sorted(f"{PLUGIN}:{int(n)}" for n in ns))
    delta = sn.Delta(uids(by_number[~was]), uids(by_number[was][differ]),
                     tuple(sn.Tombstone(u, "withdrawn") for u in uids(held.numbers[~used])))
    return _update_finish(plan, shards=shards, relations=relations, delta=delta,
                          dropped=[d for _, d in dropped], install=None if recheck else src.install,
                          key=key, now=now, log=log, dry_run=dry_run)


def _update_finish(plan: _UpdatePlan, *, shards, relations, delta, dropped: Sequence[str], install,
                   key, now, log, dry_run: bool) -> Update:
    """What `update` does once its parts are in staging: the result, the
    period and dry-run returns, the claims file kept (`install` writes the
    new one; None when re-checking), the parts promoted, the superseded kept,
    the snapshot written and signed."""
    from . import shard as shard_mod, snapshot as sn
    root, name, codex, shard_tree, prev, sid = (plan.root, plan.name, plan.codex, plan.shard_tree,
                                                plan.prev, plan.sid)
    staging, origin_dir = plan.staging, plan.origin_dir
    against_text = ", ".join(f"{k} {v}" for k, v in plan.against_of.items())
    old = {(e.slice, e.n): e.root for e in prev.shards}
    old_rel = {(e.slice, e.n): e.root for e in prev.relations}
    reissued = tuple(e.path for e in shards if old.get((e.slice, e.n)) != e.root) + \
        tuple(e.path for e in relations if old_rel.get((e.slice, e.n)) != e.root)
    result = Update(sid, prev.snapshot_id, plan.against_of, len(delta.added), len(delta.changed),
                    len(delta.tombstoned), tuple(dropped), reissued)
    if plan.period_refusal is not None and (delta.added or delta.changed or delta.tombstoned):
        shutil.rmtree(staging)
        raise plan.period_refusal
    if dry_run or not (delta.added or delta.changed or delta.tombstoned):
        shutil.rmtree(staging)
        if not (delta.added or delta.changed or delta.tombstoned):
            # nothing to write: it stays at the previous snapshot (the edges
            # name their snapshot, so a relations part would differ in that alone)
            result = dataclasses.replace(result, snapshot_id=prev.snapshot_id, reissued=())
            if not dry_run:
                log(f"[annotations] {name}: nothing to update — the claims and the records they name "
                    f"({against_text}) are as {prev.snapshot_id} holds them")
        return result

    # the claims file replaced is kept, named for the last snapshot it was current in
    if install is not None:
        cur = origin_dir / CLAIMS_FILE
        kept = origin_dir / f"claims.{prev.snapshot_id}.jsonl"
        if cur.is_file() and not kept.exists():
            cur.replace(kept)
        origin_dir.mkdir(parents=True, exist_ok=True)
        tmp = origin_dir / (CLAIMS_FILE + ".tmp")
        install(tmp)
        tmp.replace(cur)
    for entry in list(shards) + list(relations):
        dst = shard_tree / entry.path
        if (staging / entry.path).is_file() and not (dst.is_file() and entry.path not in reissued):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shard_mod.promote(staging / entry.path, dst, shard_dir=shard_tree, new_root=entry.root)
    shutil.rmtree(staging, ignore_errors=True)
    # a part the new snapshot no longer lists (fewer claims, fewer parts) is
    # the previous snapshot's last version of it: kept under that name too
    listed = {e.path for e in list(shards) + list(relations)}
    for e in list(prev.shards) + list(prev.relations):
        p_ = shard_tree / e.path
        if e.path not in listed and p_.is_file():
            dest = shard_mod.superseded_name(p_, prev.snapshot_id)
            if not dest.exists():
                p_.replace(dest)
    snap = sn.Snapshot(
        collection_id=codex.collection_id, snapshot_id=sid, previous=prev.hash,
        shards=shards, delta=delta, relations=relations, figures=(),
        files=files_of(shard_tree, sid))
    # the canonical bytes once (`write_snapshot`): a delta may list millions
    raw = snap.canonical()
    object.__setattr__(snap, "_raw_hash", cx.digest(raw))
    out = shard_tree / shard_mod.snapshot_path(sid)
    if out.is_file() and out.read_bytes() != raw:
        raise AnnotationsError(f"snapshot {sid} exists with different bytes; a snapshot is written once (09 §9.4)")
    out.write_bytes(raw)
    if key is not None:
        sn.sign_snapshot(root, name, snap, key,
                         now=now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log(f"[annotations] {name}: snapshot {sid} after {prev.snapshot_id}, checked against {against_text}: "
        f"{len(delta.added)} added, {len(delta.changed)} re-checked or changed, "
        f"{len(delta.tombstoned)} tombstoned; {len(reissued)} part(s) re-issued; {snap.hash[:23]}…"
        + (f"; {len(dropped)} claim(s) name a record the target no longer holds" if dropped else ""))
    _index_if_large(shard_tree, snap, name, log)
    return dataclasses.replace(result, snapshot=snap)


def _index_if_large(shard_tree: Path, snap, name: str, log) -> None:
    """A LARGE snapshot's claim index (app/hold/claim_index.py), built as
    soon as the snapshot is written: without it its claims group nothing at
    search (`index_of`). Derived data: a build that fails leaves the
    snapshot as written, says so, and the worker's keeper builds it
    (`claim_index.maintain`)."""
    from . import claim_index
    if not claim_index.is_large(snap):
        return
    try:
        got = claim_index.build(shard_tree, snap)
    except Exception as e:   # noqa: BLE001 — derived: the collection stands without it
        log(f"[annotations] {name}: the claim index of {snap.snapshot_id} was not built "
            f"({type(e).__name__}: {e}); the worker builds it")
        return
    log(f"[annotations] {name}: claim index of {snap.snapshot_id} built ({got.get('claims', 0):,} claims, "
        f"{got.get('ends', 0):,} ends): searched from disk")


# --------------------------------------------------------------------------
# reading: the claims a held copy makes, by target record

@dataclass(frozen=True)
class Claim:
    uid: str                     # the claim record's uid
    claim: dict[str, Any]


class ClaimIndex:
    """target uid -> the claims naming it, from a snapshot's parts. Small by
    nature — a claim per linked pair — and rebuilt only when the snapshot
    changes."""

    def __init__(self, snapshot_id: str | None, claims: Sequence[Claim]) -> None:
        self.snapshot_id = snapshot_id
        self.claims = tuple(claims)
        #: target record uid -> the WORK claims (2026-10-09) naming it, what
        #: the Ask side attaches today (app/ask/annotations.py, app/ask/works.py)
        self.by_target: dict[str, list[Claim]] = {}
        #: target record uid -> the GRAPH claims (2026-10-20) naming it — a
        #: mention by its chunk's record and its entity, a relation by its
        #: entities, a community by its members; attached by step 3 of
        #: docs/notes/wiki-gutenberg-amendments.md
        self.graph: dict[str, list[Claim]] = {}
        for c in self.claims:
            into = self.by_target if c.claim["type"] in WORK_TYPES else self.graph
            for uid in record_ends(c.claim):
                into.setdefault(uid, []).append(c)


#: SHARD path -> (snapshot hash, its index). Keyed on the PATH (CLAUDE.md).
_indexes: dict[str, tuple[str, Any]] = {}
#: SHARD path -> the snapshot hash whose missing disk index was said
_unbuilt_said: dict[str, str] = {}


def index_of(shard_tree: Path) -> ClaimIndex:
    """The claims of the held copy's current snapshot; a tombstoned claim
    is not among them (02 §2.2)."""
    from app.pack import records as pack_records
    from app.share import seeder
    # cached by the file's identity: every search asks, twice (work groups and
    # the claims beside the hits) — two uncached loads a warm search
    # (München, 2026-10-23). An annotations snapshot's delta is its claims'
    # uids, small, so holding it is harmless (unlike pubmed's, seeder.py)
    snap = seeder.cached_snapshot(Path(shard_tree))
    if snap is None:
        return ClaimIndex(None, ())
    hit = _indexes.get(str(shard_tree))
    if hit is not None and hit[0] == snap.hash:
        return hit[1]
    from app.hold import claim_index
    if claim_index.is_large(snap):
        # A LARGE snapshot (wikipedia-articles: 12.3 M claims) is read from
        # its index on disk, never loaded here (app/hold/claim_index.py);
        # until it is built, its claims apply to nothing — said once
        disk = claim_index.load(Path(shard_tree), snap)
        if disk is None:
            if _unbuilt_said.get(str(shard_tree)) != snap.hash:
                _unbuilt_said[str(shard_tree)] = snap.hash
                name = Path(shard_tree).parent.name
                print(f"annotations: {name} holds {snap.records:,} claims; their index is not built yet, "
                      f"so they group nothing until `louise hold annotations index {name}` (or the "
                      f"worker) builds it", flush=True)
            return ClaimIndex(snap.snapshot_id, ())
        _indexes[str(shard_tree)] = (snap.hash, disk)
        return disk
    gone = {t.uid for t in snap.delta.tombstoned}
    claims = []
    for s in snap.shards:
        p = Path(shard_tree) / s.path
        if not p.is_file():
            continue                     # a part this peer does not hold: its claims are not here
        for r in pack_records.iter_records(p):
            if r["uid"] not in gone:
                claims.append(Claim(r["uid"], claim_of(r)))
    idx = ClaimIndex(snap.snapshot_id, claims)
    _indexes[str(shard_tree)] = (snap.hash, idx)
    return idx


def applies_from(from_snapshot: str, read_snapshot: str | None) -> bool:
    """Is an answer read from `read_snapshot` of the target one these
    annotations fit? From `from_snapshot` onwards (both ids are the
    target's, one cadence, and sort as they run — 09 §9.4)."""
    return read_snapshot is not None and read_snapshot >= from_snapshot


def state_of(claim: dict[str, Any], now: dict[str, str | None]) -> str:
    """`current` when both pinned records' content hashes are still the ones
    the claim was checked against, `stale` when one is not, `unknown` when
    this peer's manifest carries no hash to compare — or when the claim pins
    no record (a community, 09 §9.5). `now` is keyed by record uid (a
    mention's chunk record, an entity's record uid)."""
    if claim["type"] == COMMUNITY:
        return UNKNOWN
    ends = ends_of(claim)
    a, b = now.get(end_uid(*ends[0])), now.get(end_uid(*ends[1]))
    if a is None or b is None:
        return UNKNOWN
    return CURRENT if (a, b) == (claim["subject_hash"], claim["object_hash"]) else STALE
