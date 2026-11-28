"""THE NAME A PAGE SHOWS FOR A COLLECTION, apart from its folders (operator,
2026-11-13: "the name tag shall be independent from the folders naming … It
shall be univoquely related to the collection itself; I shall be able to
rename the 'gui' tag without affecting the whole structure"; "It is ok to use
the codex name, but considering that all the sisters share the same name,
some univoque (post fix?) tag shall be added"; "No, chunking may differ").

A collection has two names:

- its KEY, the local name: the folder under `collections/`, its file
  `collections/<key>.toml`, every path, manifest row, cache key, lock and
  log line, the command line's argument and the API's `name`. Renaming it is
  `hold collection rename`, a structural operation (52.9 M path rows for
  wikipedia), never a display change;
- its DISPLAY NAME, what the Library, Ask, System and the reader show — this
  module, the one resolution every page uses:
    1. the collection file's `title` (07 §7.8: the operator's own label on
       this peer, never published) — what the card's "rename…" writes;
    2. else the Codex's `descriptive.title` up to its first " — "
       ("Normattiva — the laws of the Italian State, every version" →
       "Normattiva");
    3. else the key.
  Where two collections held here would show the same name (compared
  without case), each is followed by " · " and ITS ENCODER'S NAME (operator,
  2026-11-28, choice A: "Project Gutenberg · a96778" and "· 4048ce" were read
  one for the other, the one fully embedded for the one just begun): the
  Codex's `space.encoder.label`, its organisation and its parenthesis left
  out, in as few of its words as tell the sisters apart — "Project Gutenberg
  · Nemotron", "· Qwen3". Read from the Codex each time, so a refit's new
  encoder renames it by itself. Sisters with ONE encoder differ by something
  else (operator, 2026-11-13: "No, chunking may differ"), so theirs is
  followed by " · " and the first `POSTFIX_HEX` hexadecimal characters of its
  collection_id, more where those agree; so is a collection whose Codex names
  no encoder. The operator's own title replaces the postfix wherever other
  words are wanted, since a unique title needs none.

STANDARD NOT ADOPTED, written down (CLAUDE.md): this is the label/identifier
split of every catalogue (a DOI and its title; a Kubernetes object's `name`
and its `displayName` annotation; Git's ref and its description). The
postfix is Git's own rule for short object names — the shortest unique
prefix of the hash, at least a fixed length (`core.abbrev`) — applied to the
collection_id.
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Iterable, NamedTuple

#: the separator a Codex title puts between the name and its description
TITLE_SEPARATOR = " — "
#: the collection_id's hexadecimal characters a shared name is followed by
#: at least (Git's `core.abbrev` default is 7 for a repository's objects; a
#: node holds tens of collections, so 6 never collide in practice)
POSTFIX_HEX = 6
POSTFIX_SEPARATOR = " · "
#: how long a page process keeps the labels it resolved; `invalidate` after
#: a write through the file's writer makes the change show at once
CACHE_S = 30.0


class Entry(NamedTuple):
    key: str                       # the local name
    collection_id: str | None      # "sha256:<hex>", or None (no Codex)
    file_title: str | None         # the collection file's `title`
    codex_title: str | None        # the Codex's descriptive.title
    encoder: str | None = None     # the Codex's space.encoder.label


def encoder_words(label: str | None) -> list[str]:
    """The words of an encoder's label that name it: without its parenthesis
    and its organisation — "nvidia/Nemotron-3-Embed-1B-BF16 (c0c9fea9; …)" is
    ["Nemotron", "3", "Embed", "1B", "BF16"]."""
    if not label:
        return []
    name = label.split("(", 1)[0].strip().rsplit("/", 1)[-1]
    return [w for w in re.split(r"[-_\s]+", name) if w]


def short_title(codex_title: str | None) -> str:
    """The Codex's title up to its first " — ": the name, not the description."""
    if not codex_title:
        return ""
    return codex_title.split(TITLE_SEPARATOR, 1)[0].strip()


def base_name(e: Entry) -> str:
    """The name before any postfix: the file's title, the Codex's, the key."""
    return (e.file_title or "").strip() or short_title(e.codex_title) or e.key


def _hex(e: Entry) -> str:
    cid = e.collection_id or ""
    return cid.split(":", 1)[1] if ":" in cid else (cid or e.key)


def labels(entries: Iterable[Entry]) -> dict[str, str]:
    """Every key's display name, unique among `entries` (the collections
    held here). Pure: the same entries give the same names."""
    entries = list(entries)
    groups: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        groups[base_name(e).casefold()].append(e)
    out: dict[str, str] = {}
    for members in groups.values():
        if len(members) == 1:
            out[members[0].key] = base_name(members[0])
            continue
        for e, postfix in _postfixes(members).items():
            out[e.key] = f"{base_name(e)}{POSTFIX_SEPARATOR}{postfix}"
    # a title that happens to read like another's postfixed name: the key
    # tells them apart, so the names stay unique whatever the titles say
    seen: dict[str, list[str]] = defaultdict(list)
    for k, v in out.items():
        seen[v.casefold()].append(k)
    for keys in seen.values():
        if len(keys) > 1:
            for k in keys:
                out[k] = f"{out[k]}{POSTFIX_SEPARATOR}{k}"
    return out


def _hex_postfixes(members: list[Entry]) -> dict[Entry, str]:
    """Each member's collection_id, at its shortest distinguishing prefix,
    `POSTFIX_HEX` at least (Git's abbreviated object names)."""
    n = POSTFIX_HEX
    longest = max(len(_hex(e)) for e in members)
    while n < longest and len({_hex(e)[:n] for e in members}) < len(members):
        n += 1
    return {e: _hex(e)[:n] for e in members}


def _postfixes(members: list[Entry]) -> dict[Entry, str]:
    """What follows a shared name: each member's encoder, in the fewest of its
    words that no member with ANOTHER encoder shares; where members share an
    encoder, that and their collection_id's prefix; with no encoder named,
    the prefix alone."""
    words = {e: [w.casefold() for w in encoder_words(e.encoder)] for e in members}
    shown = {e: encoder_words(e.encoder) for e in members}
    named = [e for e in members if words[e]]
    out: dict[Entry, str] = {}
    for e in named:
        others = [o for o in named if o is not e and words[o] != words[e]]
        n = next((n for n in range(1, len(words[e]) + 1)
                  if not any(words[o][:n] == words[e][:n] for o in others)), len(words[e]))
        out[e] = "-".join(shown[e][:n])
    alike: dict[tuple, list[Entry]] = defaultdict(list)
    for e in named:
        alike[tuple(words[e])].append(e)
    for group in alike.values():
        if len(group) > 1:
            for e, hx in _hex_postfixes(group).items():
                out[e] = f"{out[e]}{POSTFIX_SEPARATOR}{hx}"
    unnamed = [e for e in members if not words[e]]
    if unnamed:
        out.update(_hex_postfixes(unnamed))
    return out


def entry_of(collection) -> Entry:
    codex = getattr(collection, "codex", None)
    encoder = getattr(getattr(getattr(codex, "space", None), "encoder", None), "label", None)
    return Entry(collection.name, getattr(codex, "collection_id", None), getattr(collection, "title", None),
                 getattr(getattr(codex, "descriptive", None), "title", None), encoder or None)


def resolve(name: str, entries: Iterable[Entry]) -> list[str]:
    """The keys a display name the operator typed names: its label, or its
    name before the postfix — compared without case. One key, or several
    when the name is shared (the caller says which), or none."""
    entries = list(entries)
    want = name.strip().casefold()
    named = labels(entries)
    exact = [k for k, v in named.items() if v.casefold() == want]
    if exact:
        return exact
    return [e.key for e in entries if base_name(e).casefold() == want]


_lock = threading.Lock()
#: (the collections directory, when resolved, the names) — keyed on the
#: directory's path, so names never carry from one node root to another
_cache: tuple[str, float, dict[str, str]] | None = None


def held() -> dict[str, str]:
    """The display names of every collection held here — the served ones and
    the annotations collections — cached `CACHE_S` per process. Never raises:
    a page without names shows keys."""
    global _cache
    from . import collections as collections_mod
    try:
        where = str(collections_mod.collections_dir())
    except Exception:  # noqa: BLE001
        return {}
    with _lock:
        if _cache is not None and _cache[0] == where and time.monotonic() - _cache[1] < CACHE_S:
            return _cache[2]
    try:
        served, _ = collections_mod.load_all(skip_unreadable=True)
        found = labels([entry_of(c) for c in [*served, *collections_mod.load_annotations()]])
    except Exception:  # noqa: BLE001 — names are a courtesy; a key is always right
        found = {}
    with _lock:
        _cache = (where, time.monotonic(), found)
    return found


def label(key: str | None) -> str:
    """One key's display name; the key itself when it is not held here."""
    if not key:
        return key or ""
    return held().get(key, key)


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None
